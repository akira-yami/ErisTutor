from __future__ import annotations

from types import SimpleNamespace

from runtime.policy_settings import RuntimeObservabilitySettings
from runtime.readiness import ReadinessStatus, RuntimeReadiness
from runtime.runtime_diagnostics import RuntimeDiagnostics
from runtime.runtime_slo import RuntimeSLOTracker
from runtime.subsystem_health import SubsystemHealthRegistry


def _readiness(status: ReadinessStatus, *reasons: str) -> RuntimeReadiness:
    return RuntimeReadiness(
        status=status,
        ready=status == ReadinessStatus.READY,
        reasons=tuple(reasons),
    )


def test_slo_tracker_measures_time_weighted_ready_ratio() -> None:
    settings = RuntimeObservabilitySettings(
        slo_window_seconds=100.0,
        slo_max_samples=100,
        minimum_ready_ratio=0.8,
        maximum_degraded_seconds=50.0,
    )
    tracker = RuntimeSLOTracker(settings=settings, clock=lambda: 100.0)
    tracker.record(_readiness(ReadinessStatus.READY), now=0.0)
    tracker.record(_readiness(ReadinessStatus.DEGRADED, "provider"), now=80.0)

    snapshot = tracker.snapshot(now=100.0)
    assert snapshot["ready_ratio"] == 0.8
    assert snapshot["status_seconds"]["ready"] == 80.0
    assert snapshot["status_seconds"]["degraded"] == 20.0
    assert snapshot["breached"] is False


def test_slo_tracker_opens_escalates_and_closes_incident() -> None:
    tracker = RuntimeSLOTracker(
        settings=RuntimeObservabilitySettings(maximum_degraded_seconds=10.0),
        clock=lambda: 50.0,
    )
    tracker.record(_readiness(ReadinessStatus.DEGRADED, "transport"), now=10.0)
    tracker.record(_readiness(ReadinessStatus.FAILED, "provider"), now=15.0)
    open_snapshot = tracker.snapshot(now=25.0)
    assert open_snapshot["open_incident"]["highest_severity"] == "failed"
    assert open_snapshot["open_incident"]["duration_seconds"] == 15.0
    assert "current_degradation_too_long" in open_snapshot["breaches"]

    tracker.record(_readiness(ReadinessStatus.READY), now=30.0)
    closed = tracker.snapshot(now=31.0)
    assert closed["open_incident"] == {}
    assert closed["recent_incidents"][-1]["ended_at"] == 30.0
    assert closed["recent_incidents"][-1]["duration_seconds"] == 20.0


def test_slo_tracker_prunes_samples_by_window_and_capacity() -> None:
    tracker = RuntimeSLOTracker(
        settings=RuntimeObservabilitySettings(slo_window_seconds=10.0, slo_max_samples=3),
        clock=lambda: 20.0,
    )
    for timestamp in range(6):
        tracker.record(_readiness(ReadinessStatus.READY), now=float(timestamp))
    assert tracker.snapshot(now=6.0)["sample_count"] == 3
    assert tracker.snapshot(now=20.0)["sample_count"] == 0


def test_runtime_diagnostics_accumulates_slo_and_incident_history() -> None:
    clock = {"now": 1.0}
    health = SubsystemHealthRegistry(clock=lambda: clock["now"])
    health.record_success("runtime.coordinator")
    health.record_success("ai_orchestrated.runtime")
    owner = SimpleNamespace(
        bootstrap_completed_phases=("a", "b", "c", "d", "e", "f"),
        ai_runtime_enabled=True,
        subsystem_health_registry=health,
        subsystem_supervisor=None,
        transport_boundary=None,
        incoming_dispatcher=None,
        loop_guard=None,
        ai_engagement_runtime=None,
        runtime_policy_settings=SimpleNamespace(
            observability=RuntimeObservabilitySettings(
                slo_window_seconds=100.0,
                minimum_ready_ratio=0.9,
                maximum_degraded_seconds=20.0,
            )
        ),
    )
    diagnostics = RuntimeDiagnostics(owner, clock=lambda: clock["now"])
    healthy = diagnostics.snapshot()
    assert healthy["schema_version"] == 7
    assert healthy["slo"]["current_status"] == "ready"

    clock["now"] = 10.0
    health.record_failure("runtime.optional", RuntimeError("oops"), degraded=True)
    degraded = diagnostics.snapshot()
    assert degraded["readiness"]["status"] == "degraded"
    assert degraded["slo"]["open_incident"]

    clock["now"] = 20.0
    health.record_success("runtime.optional")
    recovered = diagnostics.snapshot()
    assert recovered["readiness"]["status"] == "ready"
    assert recovered["slo"]["open_incident"] == {}
    assert recovered["slo"]["recent_incidents"]
