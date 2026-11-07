from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from runtime.engagements import EngagementRuntime
from runtime.engagements.registry import EngagementRegistry
from runtime.policy_settings import EngagementSettings
from runtime.readiness import ReadinessStatus, RuntimeReadinessEvaluator


def _runtime(*, max_events: int = 24, **overrides) -> EngagementRuntime:
    return EngagementRuntime(
        clock=lambda: 100.0,
        settings=EngagementSettings(max_events=max_events, **overrides),
    )


def _seed(runtime: EngagementRuntime):
    engagement = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert engagement is not None
    runtime.emit_domain_event(
        "code.tests_failed",
        engagement_id=engagement.engagement_id,
        payload={"suite": "unit"},
        now=2.0,
    )
    return engagement


def test_event_stream_is_hash_chained_and_round_trips() -> None:
    runtime = _runtime()
    _seed(runtime)

    snapshot = runtime.registry.snapshot()
    events = snapshot["events"]
    assert snapshot["schema_version"] == 7
    assert events
    assert all(len(event["event_hash"]) == 64 for event in events)
    for previous, current in zip(events, events[1:]):
        assert current["prev_hash"] == previous["event_hash"]
    assert snapshot["event_chain_head"] == events[-1]["event_hash"]

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    audit = restored.registry.audit_event_stream()
    assert audit.valid is True
    assert audit.checked_events == len(events)
    assert restored.registry.snapshot()["event_chain_head"] == snapshot["event_chain_head"]


def test_legacy_v18_snapshot_is_migrated_to_integrity_chain() -> None:
    runtime = _runtime()
    _seed(runtime)
    legacy = deepcopy(runtime.registry.snapshot())
    legacy["schema_version"] = 4
    legacy.pop("event_chain_anchor", None)
    legacy.pop("event_chain_head", None)
    legacy.pop("consumers", None)
    legacy.pop("dead_letters", None)
    for event in legacy["events"]:
        event.pop("prev_hash", None)
        event.pop("event_hash", None)

    restored = EngagementRuntime(initial_state=legacy, clock=lambda: 100.0)
    assert restored.registry.audit_event_stream().valid is True
    assert restored.registry.snapshot()["schema_version"] == 7


def test_event_tampering_is_detected_and_can_be_explicitly_rechained() -> None:
    runtime = _runtime()
    _seed(runtime)
    snapshot = deepcopy(runtime.registry.snapshot())
    snapshot["events"][-1]["payload"]["suite"] = "tampered"

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    audit = restored.registry.audit_event_stream()
    assert audit.valid is False
    assert any(error.startswith("event_hash_mismatch:") for error in audit.errors)
    assert "event_chain_head_mismatch" not in audit.errors

    repaired = restored.registry.rechain_event_stream(reason="test repair")
    assert repaired.valid is True
    assert restored.registry.validate_invariants() == ()


def test_retention_uses_chain_anchor_and_keeps_tail_verifiable() -> None:
    runtime = _runtime(max_events=24)
    engagement = _seed(runtime)
    for index in range(40):
        runtime.emit_domain_event(
            "code.file_modified",
            engagement_id=engagement.engagement_id,
            payload={"index": index},
            now=3.0 + index,
        )

    snapshot = runtime.registry.snapshot()
    assert len(snapshot["events"]) == 24
    assert snapshot["event_chain_anchor"]
    assert snapshot["events"][0]["prev_hash"] == snapshot["event_chain_anchor"]
    assert runtime.registry.audit_event_stream().valid is True


def test_compaction_does_not_punch_holes_in_integrity_stream() -> None:
    runtime = _runtime(max_terminal_engagements=0, terminal_retention_seconds=0.0)
    engagement = runtime.observe_user_message("Theo dõi build này cho tôi", now=1.0)
    assert engagement is not None
    runtime.emit_domain_event("build.completed", engagement_id=engagement.engagement_id, now=2.0)
    runtime.registry.append_event(engagement.engagement_id, "assistant.spoke", now=3.0)
    before = runtime.registry.event_cursor

    assert runtime.registry.compact(
        now=100.0,
        max_terminal_engagements=0,
        terminal_retention_seconds=0.0,
    ) == (engagement.engagement_id,)
    assert runtime.registry.event_cursor == before
    assert runtime.registry.audit_event_stream().valid is True


def test_delivery_lease_is_stable_until_acknowledged() -> None:
    runtime = _runtime(delivery_lease_seconds=30.0)
    _seed(runtime)

    first = runtime.lease_events("worker", now=10.0)
    repeated = runtime.lease_events("worker", now=20.0)
    assert first.events
    assert repeated.events == first.events
    assert repeated.next_cursor == first.next_cursor
    assert repeated.leased_until == first.leased_until
    assert repeated.delivery_attempt == 1

    acknowledged = runtime.acknowledge_events("worker", first.next_cursor, now=21.0)
    assert acknowledged == first.next_cursor
    assert runtime.lease_events("worker", now=22.0).events == ()
    state = runtime.registry.consumer_state("worker")
    assert state.delivery_attempt == 0
    assert state.last_success_at == 21.0


def test_delivery_failure_backs_off_then_releases_same_batch() -> None:
    runtime = _runtime(
        delivery_lease_seconds=5.0,
        delivery_backoff_initial_seconds=4.0,
        delivery_backoff_max_seconds=20.0,
    )
    _seed(runtime)
    first = runtime.lease_events("worker", now=10.0)
    failed = runtime.fail_delivery("worker", RuntimeError("boom"), now=11.0)
    assert failed.next_retry_at == 15.0

    blocked = runtime.lease_events("worker", now=12.0)
    assert blocked.events == ()
    assert blocked.retry_after_seconds == 3.0

    retried = runtime.lease_events("worker", now=15.0)
    assert retried.events == first.events
    assert retried.delivery_attempt == 2


def test_poison_delivery_moves_to_bounded_dead_letter_ledger() -> None:
    runtime = _runtime(
        delivery_max_attempts=2,
        delivery_backoff_initial_seconds=0.0,
        max_dead_letters=2,
    )
    _seed(runtime)
    first = runtime.lease_events("worker", now=10.0)
    runtime.fail_delivery("worker", "first failure", now=11.0)
    second = runtime.lease_events("worker", now=11.0)
    assert second.delivery_attempt == 2
    state = runtime.fail_delivery("worker", "poison", now=12.0)

    assert state.acknowledged_cursor == first.next_cursor
    assert state.dead_letter_count == 1
    letters = runtime.registry.dead_letters("worker")
    assert len(letters) == 1
    assert letters[0].from_cursor == first.requested_cursor
    assert letters[0].to_cursor == first.next_cursor
    assert letters[0].event_hashes
    assert runtime.lease_events("worker", now=13.0).events == ()


def test_delivery_state_and_dead_letters_survive_restart() -> None:
    runtime = _runtime(delivery_max_attempts=1)
    _seed(runtime)
    batch = runtime.lease_events("worker", now=10.0)
    runtime.fail_delivery("worker", "poison", now=11.0)
    snapshot = runtime.registry.snapshot()

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    assert restored.registry.consumer_cursor("worker") == batch.next_cursor
    assert restored.registry.consumer_state("worker").dead_letter_count == 1
    assert len(restored.registry.dead_letters("worker")) == 1
    assert restored.registry.audit_event_stream().valid is True


def test_old_consumer_cursor_map_migrates_to_consumer_state() -> None:
    runtime = _runtime()
    _seed(runtime)
    snapshot = runtime.registry.snapshot()
    snapshot.pop("consumers", None)
    snapshot["consumer_cursors"] = {"legacy": 1}

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    assert restored.registry.consumer_state("legacy").acknowledged_cursor == 1


def test_readiness_degrades_on_integrity_failure_and_dead_letters() -> None:
    evaluator = RuntimeReadinessEvaluator(critical_subsystems=())
    base = {
        "health": {"overall": "healthy", "subsystems": []},
        "bootstrap": {"ai_runtime_enabled": True, "completed_phases": list(range(6))},
        "boundaries": {},
        "engagement": {
            "kernel": {
                "invariant_violations": [],
                "event_integrity": {"valid": False},
                "dead_letter_count": 1,
            }
        },
    }
    readiness = evaluator.evaluate(base)
    assert readiness.status == ReadinessStatus.DEGRADED
    assert "engagement_event_integrity" in readiness.degraded_components
    assert "engagement_dead_letters" in readiness.degraded_components


@pytest.mark.parametrize("bad_cursor", [-1, 10_000])
def test_reset_consumer_clamps_cursor(bad_cursor: int) -> None:
    runtime = _runtime()
    _seed(runtime)
    state = runtime.registry.reset_consumer("worker", cursor=bad_cursor)
    assert 0 <= state.acknowledged_cursor <= runtime.registry.event_cursor


def test_complete_event_replay_matches_adapter_projection() -> None:
    runtime = _runtime(max_events=64)
    engagement = _seed(runtime)
    runtime.emit_domain_event(
        "code.file_modified",
        engagement_id=engagement.engagement_id,
        payload={"file": "service.py"},
        now=3.0,
    )
    report = runtime.registry.replay_engagement(engagement.engagement_id)
    assert report.complete is True
    assert report.state_matches is True
    assert report.replayed_events == runtime.registry.get(engagement.engagement_id).event_seq


def test_projection_replay_detects_and_repairs_corrupt_snapshot_state() -> None:
    runtime = _runtime(max_events=64)
    engagement = _seed(runtime)
    snapshot = deepcopy(runtime.registry.snapshot())
    snapshot["engagements"][0]["domain_state"]["test_status"] = "invented"

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    report = restored.registry.replay_engagement(engagement.engagement_id)
    assert report.complete is True
    assert report.state_matches is False
    assert f"projection_replay_mismatch:{engagement.engagement_id}" in restored.registry.validate_invariants()

    repaired = restored.registry.repair_engagement_projection(engagement.engagement_id)
    assert repaired.complete is True
    assert repaired.state_matches is True
    assert restored.registry.validate_invariants() == ()


def test_projection_replay_refuses_to_guess_after_prefix_retention() -> None:
    runtime = _runtime(max_events=24)
    engagement = _seed(runtime)
    for index in range(40):
        runtime.emit_domain_event(
            "code.file_modified",
            engagement_id=engagement.engagement_id,
            payload={"index": index},
            now=3.0 + index,
        )
    report = runtime.registry.replay_engagement(engagement.engagement_id)
    assert report.complete is False
    assert report.reason == "retained_prefix_incomplete"
    assert runtime.registry.repair_engagement_projection(engagement.engagement_id) == report
