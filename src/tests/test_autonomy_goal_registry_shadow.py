"""Offline shadow-mode proof: no live goal writes and no action/speech authority."""
from __future__ import annotations

from dataclasses import replace

import pytest

from runtime.autonomy.goal_registry import ShadowGoalRegistry
from runtime.goal_lifecycle import GoalLifecycleRecord, GoalLifecycleState, reconcile_goal_records


def _goal(text="Investigate tests", owner="eris", now=100.0):
    return GoalLifecycleRecord.create(text, owner=owner, now=now, turn_seq=1)


def _registry(tmp_path, **kwargs):
    return ShadowGoalRegistry(tmp_path / "shadow-only.sqlite", clock=lambda: 100.0, **kwargs)


def test_eris_goal_does_not_disappear_from_shadow_after_unconfirmed_chat(tmp_path):
    registry = _registry(tmp_path)
    goal = _goal()
    assert registry.observe_legacy([goal], source_revision=1, observation_id="turn:1").applied
    last = (goal,)
    for turn in range(2, 6):
        last = reconcile_goal_records(
            last, open_loop="", owner="unknown", event_kind="chat",
            relationship_meaning="neutral", confidence=0.9, turn_seq=turn,
            evidence_refs=(), now=100.0 + turn,
        )
        result = registry.observe_legacy(last, source_revision=turn, observation_id=f"turn:{turn}")
        assert result.applied
    assert last[0].state == GoalLifecycleState.EXPIRED
    shadow_rev, observed = registry.snapshot()
    assert shadow_rev == 5
    assert observed[0].legacy_state == "expired"
    assert observed[0].shadow_expected_state == "active"
    assert any(diff.code == "self_goal_legacy_decay" for diff in result.diffs)
    # Source remains completely unaffected by shadow observation.
    assert last[0].state == GoalLifecycleState.EXPIRED


def test_user_owned_goal_is_mirrored_without_self_goal_override(tmp_path):
    registry = _registry(tmp_path)
    item = _goal(owner="user")
    registry.observe_legacy([item], source_revision=1, observation_id="turn:1")
    item = replace(item, state=GoalLifecycleState.SUSPENDED, reason="not_reconfirmed")
    registry.observe_legacy([item], source_revision=2, observation_id="turn:2")
    _, snapshot = registry.snapshot()
    assert snapshot[0].legacy_state == "suspended"
    assert snapshot[0].shadow_expected_state == "suspended"


def test_intentional_abandonment_is_respected_even_for_eris(tmp_path):
    registry = _registry(tmp_path)
    goal = _goal()
    registry.observe_legacy([goal], source_revision=1, observation_id="turn:1")
    registry.observe_legacy(
        [replace(goal, state=GoalLifecycleState.ABANDONED, reason="semantic_abandonment")],
        source_revision=2, observation_id="turn:2",
    )
    _, snapshot = registry.snapshot()
    assert snapshot[0].shadow_expected_state == "abandoned"


def test_missing_goal_is_retained_as_possible_bounded_projection_loss(tmp_path):
    registry = _registry(tmp_path)
    goal = _goal()
    registry.observe_legacy([goal], source_revision=10, observation_id="v10")
    result = registry.observe_legacy([], source_revision=11, observation_id="v11")
    assert any(x.code == "missing_from_legacy_projection" for x in result.diffs)
    _, (record,) = registry.snapshot()
    assert record.goal_id == goal.goal_id
    assert record.missing_since_revision == 11
    assert record.shadow_expected_state == "active"
    again = registry.observe_legacy([], source_revision=12, observation_id="v12")
    assert not any(x.code == "missing_from_legacy_projection" for x in again.diffs)


def test_duplicate_or_out_of_order_source_revision_cannot_overwrite(tmp_path):
    registry = _registry(tmp_path)
    goal = _goal()
    a = registry.observe_legacy([goal], source_revision=10, observation_id="event10")
    b = registry.observe_legacy([], source_revision=10, observation_id="event10")
    c = registry.observe_legacy([], source_revision=9, observation_id="event9")
    d = registry.observe_legacy([], source_revision=10, observation_id="conflicting")
    assert a.applied is True
    assert b.status == "duplicate"
    assert c.status == "stale"
    assert d.status == "stale"
    assert b.shadow_revision == c.shadow_revision == d.shadow_revision == 1
    assert registry.snapshot()[1][0].legacy_state == "active"


def test_restart_can_recover_shadow_snapshot(tmp_path):
    goal = _goal()
    _registry(tmp_path).observe_legacy([goal], source_revision=7, observation_id="s7")
    new_process_like_instance = _registry(tmp_path)
    revision, goals = new_process_like_instance.snapshot()
    assert revision == 1
    assert goals[0].goal_id == goal.goal_id


def test_duplicate_goal_id_is_rejected(tmp_path):
    goal = _goal()
    with pytest.raises(ValueError, match="duplicate goal_id"):
        _registry(tmp_path).observe_legacy([goal, goal], source_revision=1, observation_id="1")


def test_max_capacity_rejects_instead_of_silently_dropping(tmp_path):
    registry = _registry(tmp_path, max_known=1)
    one = _goal("one")
    two = _goal("two")
    with pytest.raises(RuntimeError, match="max_known"):
        registry.observe_legacy([one, two], source_revision=1, observation_id="1")
    assert registry.snapshot()[1] == ()


def test_ownership_transition_has_diagnostic_evidence(tmp_path):
    registry = _registry(tmp_path)
    user = _goal(owner="user")
    registry.observe_legacy([user], source_revision=1, observation_id="v1")
    change = registry.observe_legacy(
        [replace(user, owner="eris")], source_revision=2, observation_id="v2"
    )
    assert any(x.code == "owner_changed_in_legacy" for x in change.diffs)
    assert registry.snapshot()[1][0].owner == "eris"


def test_concurrent_out_of_order_snapshots_preserve_highest_source_revision(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    registry = _registry(tmp_path)
    goal = _goal()

    def observe(revision):
        return registry.observe_legacy(
            [goal], source_revision=revision, observation_id=f"revision:{revision}"
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        reports = list(executor.map(observe, reversed(range(1, 33))))
    assert any(item.applied for item in reports)
    persisted = registry._store.load(registry._state_key)
    assert persisted is not None
    assert persisted.payload["last_source_revision"] == 32
    assert len(registry.snapshot()[1]) == 1
