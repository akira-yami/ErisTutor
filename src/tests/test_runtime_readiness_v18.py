from __future__ import annotations

from types import SimpleNamespace

from runtime.readiness import ReadinessStatus, RuntimeReadinessEvaluator
from runtime.runtime_diagnostics import RuntimeDiagnostics
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry


def test_readiness_is_ready_for_healthy_runtime() -> None:
    evaluator = RuntimeReadinessEvaluator()
    result = evaluator.evaluate({
        "bootstrap": {"ai_runtime_enabled": True, "completed_phases": list(range(6))},
        "health": {"overall": "healthy", "subsystems": []},
        "boundaries": {},
        "engagement": {"kernel": {"invariant_violations": []}},
    })
    assert result.status == ReadinessStatus.READY
    assert result.ready is True


def test_readiness_fails_when_critical_subsystem_failed() -> None:
    result = RuntimeReadinessEvaluator().evaluate({
        "bootstrap": {"ai_runtime_enabled": True},
        "health": {
            "overall": "failed",
            "subsystems": [{"name": "runtime.coordinator", "status": "failed"}],
        },
        "boundaries": {},
        "engagement": {},
    })
    assert result.status == ReadinessStatus.FAILED
    assert result.critical_failures == ("runtime.coordinator",)


def test_readiness_degrades_on_engagement_invariant_or_boundary_error() -> None:
    result = RuntimeReadinessEvaluator().evaluate({
        "bootstrap": {"ai_runtime_enabled": True},
        "health": {"overall": "healthy", "subsystems": []},
        "boundaries": {"transport": {"error": "RuntimeError"}},
        "engagement": {"kernel": {"invariant_violations": ["active_suspended:x"]}},
    })
    assert result.status == ReadinessStatus.DEGRADED
    assert "boundary:transport" in result.degraded_components
    assert "engagement_invariants" in result.degraded_components


def test_runtime_diagnostics_includes_readiness_projection() -> None:
    health = SubsystemHealthRegistry(clock=lambda: 1.0)
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
    )
    snapshot = RuntimeDiagnostics(owner, clock=lambda: 2.0).snapshot(include_events=False)
    assert snapshot["schema_version"] == 7
    assert snapshot["readiness"]["status"] == "ready"


def test_health_sink_failure_is_diagnosable() -> None:
    def broken_sink(_payload):
        raise RuntimeError("sink down")

    health = SubsystemHealthRegistry(clock=lambda: 1.0, on_change=broken_sink)
    health.record_failure("provider", "offline", kind=FailureKind.PROVIDER_TRANSIENT)
    snapshot = health.snapshot(include_events=False)
    assert snapshot["last_sink_error"]["error"] == "RuntimeError"
