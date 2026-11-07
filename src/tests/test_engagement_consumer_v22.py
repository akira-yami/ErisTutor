from __future__ import annotations

from runtime.engagements import EngagementEventConsumer, EngagementRuntime
from runtime.policy_settings import EngagementSettings
from runtime.subsystem_health import HealthStatus, SubsystemHealthRegistry


def _runtime(**settings) -> EngagementRuntime:
    return EngagementRuntime(
        clock=lambda: 100.0,
        settings=EngagementSettings(max_events=64, **settings),
    )


def _seed(runtime: EngagementRuntime) -> None:
    engagement = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert engagement is not None
    runtime.emit_domain_event(
        "code.tests_failed",
        engagement_id=engagement.engagement_id,
        payload={"test_status": "failed"},
        now=2.0,
    )


def test_consumer_pump_processes_and_acknowledges_batch() -> None:
    runtime = _runtime()
    _seed(runtime)
    handled = []
    consumer = EngagementEventConsumer(
        runtime.registry,
        "projection",
        lambda batch: handled.extend(event.kind for event in batch.events),
        settings=runtime.settings,
        clock=lambda: 10.0,
    )

    result = consumer.poll_once(now=10.0)
    assert result.status == "processed"
    assert result.event_count == len(handled)
    assert runtime.registry.consumer_cursor("projection") == result.cursor
    assert consumer.poll_once(now=11.0).status == "idle"
    assert consumer.snapshot()["success_count"] == 1


def test_consumer_pump_retries_failed_handler_then_recovers() -> None:
    runtime = _runtime(
        delivery_backoff_initial_seconds=2.0,
        delivery_backoff_max_seconds=4.0,
        delivery_max_attempts=3,
    )
    _seed(runtime)
    attempts = {"count": 0}

    def handler(batch) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary")

    consumer = EngagementEventConsumer(
        runtime.registry,
        "worker",
        handler,
        settings=runtime.settings,
    )
    failed = consumer.poll_once(now=10.0)
    assert failed.status == "retry"
    assert failed.retry_after_seconds == 2.0
    assert consumer.poll_once(now=11.0).status == "backoff"
    recovered = consumer.poll_once(now=12.0)
    assert recovered.status == "processed"
    assert attempts["count"] == 2
    assert consumer.snapshot()["last_error"] == ""


def test_consumer_pump_dead_letters_poison_batch_and_reports_health() -> None:
    runtime = _runtime(
        delivery_backoff_initial_seconds=0.0,
        delivery_max_attempts=2,
        max_dead_letters=8,
    )
    _seed(runtime)
    health = SubsystemHealthRegistry(clock=lambda: 10.0)

    def poison(_batch) -> None:
        raise ValueError("poison")

    consumer = EngagementEventConsumer(
        runtime.registry,
        "poison",
        poison,
        settings=runtime.settings,
        health_registry=health,
    )
    assert consumer.poll_once(now=10.0).status == "retry"
    dead = consumer.poll_once(now=10.0)
    assert dead.status == "dead_lettered"
    assert dead.dead_lettered is True
    assert consumer.snapshot()["dead_letter_count"] == 1
    snapshot = health.get("engagement.consumer.poison")
    assert snapshot is not None
    assert snapshot.status == HealthStatus.DEGRADED


def test_consumer_pump_marks_health_healthy_after_success() -> None:
    runtime = _runtime()
    _seed(runtime)
    health = SubsystemHealthRegistry(clock=lambda: 20.0)
    consumer = EngagementEventConsumer(
        runtime.registry,
        "healthy",
        lambda _batch: None,
        settings=runtime.settings,
        health_registry=health,
    )
    result = consumer.poll_once(now=20.0)
    assert result.status == "processed"
    snapshot = health.get("engagement.consumer.healthy")
    assert snapshot is not None
    assert snapshot.status == HealthStatus.HEALTHY
    assert snapshot.details["cursor"] == result.cursor


def test_consumer_rejects_invalid_constructor_inputs() -> None:
    runtime = _runtime()
    try:
        EngagementEventConsumer(runtime.registry, "", lambda _batch: None)
    except ValueError as exc:
        assert "consumer_id" in str(exc)
    else:
        raise AssertionError("missing consumer id should fail")

    try:
        EngagementEventConsumer(runtime.registry, "x", None)  # type: ignore[arg-type]
    except TypeError as exc:
        assert "handler" in str(exc)
    else:
        raise AssertionError("non-callable handler should fail")


def test_runtime_factory_injects_shared_policy_and_health() -> None:
    from runtime.engagements.runtime import EngagementRuntime
    from runtime.subsystem_health import SubsystemHealthRegistry

    health = SubsystemHealthRegistry(clock=lambda: 1.0)
    runtime = EngagementRuntime(clock=lambda: 1.0, health_registry=health)
    consumer = runtime.create_event_consumer("projection", lambda batch: None)
    assert consumer.registry is runtime.registry
    assert consumer.settings is runtime.settings
    assert consumer.health_registry is health
    assert consumer._clock() == 1.0
