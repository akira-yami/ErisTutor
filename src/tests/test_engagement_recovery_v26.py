from __future__ import annotations

from copy import deepcopy

from runtime.engagements import EngagementEventConsumer, EngagementRuntime
from runtime.policy_settings import EngagementSettings
from runtime.readiness import ReadinessStatus, RuntimeReadinessEvaluator


def _runtime(*, max_events: int = 64, **settings) -> EngagementRuntime:
    return EngagementRuntime(
        clock=lambda: 100.0,
        settings=EngagementSettings(max_events=max_events, **settings),
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


def _readiness(runtime: EngagementRuntime):
    return RuntimeReadinessEvaluator(critical_subsystems=()).evaluate({
        "health": {"overall": "healthy", "subsystems": []},
        "bootstrap": {"ai_runtime_enabled": True, "completed_phases": list(range(6))},
        "boundaries": {},
        "engagement": runtime.diagnostics(),
    })


def test_snapshot_digest_detects_tampering_without_corrupting_event_chain() -> None:
    runtime = _runtime()
    engagement = _seed(runtime)
    snapshot = deepcopy(runtime.registry.snapshot())
    snapshot["engagements"][0]["domain_state"]["test_status"] = "invented"

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    assert restored.registry.snapshot_integrity() == {
        "valid": False,
        "error": "snapshot_digest_mismatch",
        "schema_version": 7,
    }
    assert restored.registry.audit_event_stream().valid is True
    report = restored.audit_recovery()
    assert report.status == "corrupt"
    assert report.snapshot_valid is False
    assert report.projection_mismatches == (engagement.engagement_id,)

    recovered = restored.recover(repair_projections=True, accept_snapshot=True)
    assert recovered.healthy is True
    assert recovered.repaired_projections == (engagement.engagement_id,)
    assert restored.registry.snapshot_integrity()["valid"] is True


def test_event_audit_reports_global_cursor_holes() -> None:
    runtime = _runtime()
    _seed(runtime)
    snapshot = deepcopy(runtime.registry.snapshot())
    del snapshot["events"][1]
    snapshot.pop("snapshot_digest", None)
    snapshot["schema_version"] = 5

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    audit = restored.registry.audit_event_stream()
    assert audit.valid is False
    assert any(error.startswith("event_log_gap:") for error in audit.errors)


def test_consumer_stops_on_retention_gap_until_operator_resets_cursor() -> None:
    runtime = _runtime(
        max_events=24,
        delivery_stop_on_gap=True,
        consumer_lag_warning_events=4,
    )
    engagement = _seed(runtime)
    for index in range(40):
        runtime.emit_domain_event(
            "code.file_modified",
            engagement_id=engagement.engagement_id,
            payload={"index": index},
            now=3.0 + index,
        )

    handled = []
    consumer = runtime.create_event_consumer(
        "slow-projection", lambda batch: handled.extend(batch.events)
    )
    gap = consumer.poll_once(now=50.0)
    assert gap.status == "gap"
    assert gap.has_gap is True
    assert handled == []
    blocked = consumer.poll_once(now=50.5)
    assert blocked.status == "blocked"
    assert blocked.blocked_reason
    assert runtime.registry.consumer_state("slow-projection").blocked_reason

    state = runtime.reset_consumer_to_earliest("slow-projection")
    assert state.acknowledged_cursor == runtime.registry.earliest_event_cursor - 1
    processed = consumer.poll_once(now=51.0)
    assert processed.status == "processed"
    assert handled


def test_blocked_consumer_state_survives_restart_until_reset() -> None:
    runtime = _runtime(max_events=24, delivery_stop_on_gap=True)
    engagement = _seed(runtime)
    for index in range(30):
        runtime.emit_domain_event(
            "code.file_modified", engagement_id=engagement.engagement_id,
            payload={"index": index}, now=3.0 + index,
        )
    consumer = runtime.create_event_consumer("slow", lambda _batch: None)
    assert consumer.poll_once(now=40.0).status == "gap"

    restored = EngagementRuntime(
        initial_state=runtime.registry.snapshot(), clock=lambda: 100.0,
        settings=runtime.settings,
    )
    restored_consumer = restored.create_event_consumer("slow", lambda _batch: None)
    assert restored_consumer.poll_once(now=100.0).status == "blocked"
    restored.reset_consumer_to_earliest("slow")
    assert restored_consumer.poll_once(now=101.0).status == "processed"


def test_dead_letter_captures_payload_and_can_replay_after_stream_retention() -> None:
    runtime = _runtime(
        max_events=24,
        delivery_max_attempts=1,
        max_dead_letters=8,
    )
    engagement = _seed(runtime)
    poison = EngagementEventConsumer(
        runtime.registry,
        "projection",
        lambda _batch: (_ for _ in ()).throw(ValueError("poison")),
        settings=runtime.settings,
    )
    failed = poison.poll_once(now=10.0)
    assert failed.status == "dead_lettered"
    assert failed.dead_letter_id
    letter = runtime.registry.dead_letter(failed.dead_letter_id)
    assert letter is not None and letter.open
    assert letter.events

    for index in range(50):
        runtime.emit_domain_event(
            "code.file_modified",
            engagement_id=engagement.engagement_id,
            payload={"index": index},
            now=20.0 + index,
        )
    assert letter.events[0].log_seq < runtime.registry.earliest_event_cursor

    replayed_batches = []
    recovery_consumer = EngagementEventConsumer(
        runtime.registry,
        "projection",
        lambda batch: replayed_batches.append(batch),
        settings=runtime.settings,
    )
    replay = recovery_consumer.replay_dead_letter(letter.letter_id, now=100.0)
    assert replay.status == "replayed"
    assert replay.dead_letter_id == letter.letter_id
    assert replayed_batches[0].events == letter.events
    resolved = runtime.registry.dead_letter(letter.letter_id)
    assert resolved is not None
    assert resolved.status == "resolved"
    assert resolved.replay_count == 1
    assert runtime.registry.dead_letters(status="open") == ()


def test_failed_dead_letter_replay_stays_open_and_diagnosable() -> None:
    runtime = _runtime(delivery_max_attempts=1)
    _seed(runtime)
    consumer = runtime.create_event_consumer(
        "projection", lambda _batch: (_ for _ in ()).throw(RuntimeError("down"))
    )
    dead = consumer.poll_once(now=10.0)
    replay = consumer.replay_dead_letter(dead.dead_letter_id, now=11.0)
    assert replay.status == "replay_failed"
    letter = runtime.registry.dead_letter(dead.dead_letter_id)
    assert letter is not None and letter.open
    assert letter.replay_count == 1


def test_resolved_dead_letters_do_not_keep_runtime_degraded() -> None:
    runtime = _runtime(delivery_max_attempts=1, consumer_lag_warning_events=1000)
    _seed(runtime)
    poison = runtime.create_event_consumer(
        "projection", lambda _batch: (_ for _ in ()).throw(ValueError("poison"))
    )
    result = poison.poll_once(now=10.0)
    assert _readiness(runtime).status == ReadinessStatus.DEGRADED

    runtime.resolve_dead_letter(result.dead_letter_id, now=11.0)
    readiness = _readiness(runtime)
    assert readiness.status == ReadinessStatus.READY
    assert "engagement_dead_letters" not in readiness.degraded_components


def test_delivery_pressure_reports_lag_and_retention_risk() -> None:
    runtime = _runtime(max_events=24, consumer_lag_warning_events=3)
    engagement = _seed(runtime)
    runtime.registry.reset_consumer("slow", cursor=0)
    for index in range(30):
        runtime.emit_domain_event(
            "code.file_modified",
            engagement_id=engagement.engagement_id,
            payload={"index": index},
            now=3.0 + index,
        )
    pressure = runtime.registry.diagnostics(now=100.0)["delivery_pressure"]
    assert pressure["degraded"] is True
    assert "slow" in pressure["lagging_consumers"]
    assert "slow" in pressure["at_risk_consumers"]
    assert "consumer_lag" in pressure["reasons"]
    assert "consumer_retention_gap" in pressure["reasons"]


def test_legacy_dead_letter_migrates_with_id_and_retained_replay_payload() -> None:
    runtime = _runtime(delivery_max_attempts=1)
    _seed(runtime)
    poison = runtime.create_event_consumer(
        "legacy", lambda _batch: (_ for _ in ()).throw(ValueError("poison"))
    )
    result = poison.poll_once(now=10.0)
    snapshot = deepcopy(runtime.registry.snapshot())
    snapshot["schema_version"] = 5
    snapshot.pop("snapshot_digest", None)
    raw_letter = snapshot["dead_letters"][0]
    raw_letter.pop("letter_id", None)
    raw_letter.pop("events", None)
    raw_letter.pop("status", None)
    raw_letter.pop("replay_count", None)
    raw_letter.pop("last_replayed_at", None)
    raw_letter.pop("resolved_at", None)
    raw_letter.pop("resolution", None)

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    letter = restored.registry.dead_letters("legacy")[0]
    assert letter.letter_id.startswith("dlq_")
    assert letter.open is True
    batch = restored.registry.dead_letter_batch(letter.letter_id)
    assert batch.events
    assert batch.dead_letter_id == letter.letter_id
    assert result.cursor == letter.to_cursor


def test_resolved_dead_letter_state_survives_restart() -> None:
    runtime = _runtime(delivery_max_attempts=1, consumer_lag_warning_events=1000)
    _seed(runtime)
    poison = runtime.create_event_consumer(
        "projection", lambda _batch: (_ for _ in ()).throw(ValueError("poison"))
    )
    result = poison.poll_once(now=10.0)
    runtime.resolve_dead_letter(result.dead_letter_id, resolution="operator inspected", now=11.0)

    restored = EngagementRuntime(
        initial_state=runtime.registry.snapshot(), clock=lambda: 100.0,
        settings=runtime.settings,
    )
    letter = restored.registry.dead_letter(result.dead_letter_id)
    assert letter is not None
    assert letter.status == "resolved"
    assert letter.resolution == "operator inspected"
    assert restored.registry.dead_letters(status="open") == ()
    assert _readiness(restored).status == ReadinessStatus.READY
