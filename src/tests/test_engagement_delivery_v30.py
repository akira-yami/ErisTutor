from __future__ import annotations

from copy import deepcopy

from runtime.engagements import EngagementRuntime
from runtime.policy_settings import EngagementSettings


def _runtime(**overrides) -> EngagementRuntime:
    return EngagementRuntime(
        clock=lambda: 100.0,
        settings=EngagementSettings(
            max_events=128,
            delivery_backoff_initial_seconds=0.0,
            delivery_backoff_max_seconds=0.0,
            **overrides,
        ),
    )


def _seed(runtime: EngagementRuntime, *, extra: int = 0):
    engagement = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert engagement is not None
    runtime.emit_domain_event(
        "code.tests_failed", engagement_id=engagement.engagement_id,
        payload={"index": 0}, now=2.0,
    )
    for index in range(extra):
        runtime.emit_domain_event(
            "code.file_modified", engagement_id=engagement.engagement_id,
            payload={"index": index + 1}, now=3.0 + index,
        )
    return engagement


def test_delivery_identity_is_stable_for_same_lease() -> None:
    runtime = _runtime()
    _seed(runtime)
    first = runtime.registry.lease_events("projection", now=10.0, lease_seconds=30.0)
    second = runtime.registry.lease_events("projection", now=11.0, lease_seconds=30.0)
    assert first.delivery_id.startswith("del_")
    assert first.delivery_id == second.delivery_id
    assert first.event_digest == second.event_digest
    assert first.next_cursor == second.next_cursor


def test_success_commits_receipt_and_receipt_survives_restart() -> None:
    runtime = _runtime()
    _seed(runtime)
    consumer = runtime.create_event_consumer(
        "projection", lambda batch: {"idempotency_token": "sink:42"}
    )
    result = consumer.poll_once(now=10.0)
    assert result.status == "processed"
    assert result.receipt_committed is True
    receipt = runtime.registry.delivery_receipt(result.delivery_id)
    assert receipt is not None
    assert receipt.handler_token == "sink:42"
    assert receipt.to_cursor == result.cursor

    restored = EngagementRuntime(
        initial_state=runtime.registry.snapshot(), settings=runtime.settings,
        clock=lambda: 100.0,
    )
    restored_receipt = restored.registry.delivery_receipt(result.delivery_id)
    assert restored_receipt == receipt
    assert restored.registry.consumer_cursor("projection") == result.cursor


def test_existing_receipt_suppresses_duplicate_handler_execution() -> None:
    runtime = _runtime()
    _seed(runtime)
    calls = {"count": 0}

    def handler(_batch):
        calls["count"] += 1

    consumer = runtime.create_event_consumer("projection", handler)
    processed = consumer.poll_once(now=10.0)
    assert processed.status == "processed"
    snapshot = deepcopy(runtime.registry.snapshot())
    snapshot["consumers"]["projection"]["acknowledged_cursor"] = 0
    snapshot["consumer_cursors"]["projection"] = 0
    snapshot.pop("snapshot_digest", None)
    snapshot["schema_version"] = 5

    restored = EngagementRuntime(initial_state=snapshot, settings=runtime.settings)
    duplicate = restored.create_event_consumer("projection", handler).poll_once(now=20.0)
    assert duplicate.status == "duplicate_suppressed"
    assert duplicate.delivery_id == processed.delivery_id
    assert calls["count"] == 1


def test_poison_batch_is_split_until_only_poison_event_is_dead_lettered() -> None:
    runtime = _runtime(
        event_consumer_batch_size=16,
        delivery_max_attempts=2,
        delivery_split_poison_batches=True,
        delivery_split_after_attempts=2,
    )
    _seed(runtime, extra=5)
    processed_kinds: list[str] = []

    def handler(batch):
        if any(event.kind == "code.tests_failed" for event in batch.events):
            raise ValueError("poison event")
        processed_kinds.extend(event.kind for event in batch.events)

    consumer = runtime.create_event_consumer("projection", handler)
    statuses = []
    for tick in range(30):
        result = consumer.poll_once(now=10.0 + tick)
        statuses.append(result.status)
        if result.status == "idle":
            break
    assert "split" in statuses
    assert "dead_lettered" in statuses
    assert statuses[-1] == "idle"
    letters = runtime.registry.dead_letters("projection", status="open")
    assert len(letters) == 1
    assert len(letters[0].events) == 1
    assert letters[0].events[0].kind == "code.tests_failed"
    assert "code.file_modified" in processed_kinds


def test_managed_consumer_supervisor_is_fair_and_failure_isolated() -> None:
    runtime = _runtime(managed_consumer_poll_budget=1)
    _seed(runtime)
    calls: list[str] = []
    runtime.create_event_consumer(
        "a", lambda _batch: calls.append("a"), managed=True
    )
    runtime.create_event_consumer(
        "b", lambda _batch: calls.append("b"), managed=True
    )
    first = runtime.poll_event_consumers(now=10.0)
    second = runtime.poll_event_consumers(now=11.0)
    assert first.polled == second.polled == 1
    assert calls == ["a", "b"]
    snapshot = runtime.consumer_supervisor.snapshot()
    assert snapshot["registered"] == 2
    assert snapshot["tick_count"] == 2


def test_recovery_repairs_consumer_cursor_from_committed_receipt() -> None:
    runtime = _runtime()
    _seed(runtime)
    processed = runtime.create_event_consumer("projection", lambda _batch: None).poll_once(now=10.0)
    snapshot = deepcopy(runtime.registry.snapshot())
    snapshot["consumers"]["projection"]["acknowledged_cursor"] = 0
    snapshot["consumer_cursors"]["projection"] = 0
    snapshot.pop("snapshot_digest", None)
    snapshot["schema_version"] = 5

    restored = EngagementRuntime(initial_state=snapshot, settings=runtime.settings)
    before = restored.audit_recovery()
    assert before.status == "corrupt"
    assert before.receipt_errors == (f"receipt_cursor_uncommitted:{processed.delivery_id}",)
    after = restored.recover(repair_receipt_cursors=True)
    assert after.healthy is True
    assert after.repaired_consumers == ("projection",)
    assert restored.registry.consumer_cursor("projection") == processed.cursor


def test_receipt_digest_tampering_is_detected() -> None:
    runtime = _runtime()
    _seed(runtime)
    result = runtime.create_event_consumer("projection", lambda _batch: None).poll_once(now=10.0)
    snapshot = deepcopy(runtime.registry.snapshot())
    snapshot["delivery_receipts"][0]["event_digest"] = "0" * 64
    snapshot.pop("snapshot_digest", None)
    snapshot["schema_version"] = 5

    restored = EngagementRuntime(initial_state=snapshot, settings=runtime.settings)
    audit = restored.registry.audit_delivery_receipts()
    assert audit.valid is False
    assert audit.errors == (f"receipt_digest_mismatch:{result.delivery_id}",)


def test_delivery_receipt_retention_is_bounded() -> None:
    runtime = _runtime(max_delivery_receipts=32, event_consumer_batch_size=1)
    engagement = _seed(runtime, extra=40)
    consumer = runtime.create_event_consumer("projection", lambda _batch: None)
    for tick in range(80):
        result = consumer.poll_once(now=10.0 + tick)
        if result.status == "idle":
            break
    receipts = runtime.registry.delivery_receipts("projection")
    assert len(receipts) == 32
    assert receipts[-1].to_cursor == runtime.registry.consumer_cursor("projection")
