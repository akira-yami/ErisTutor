from __future__ import annotations

import pytest

from runtime.subsystem_health import FailureKind, HealthStatus, SubsystemHealthRegistry

from .clock import FakeClock


def test_health_registry_records_degradation_and_recovery() -> None:
    clock = FakeClock(10.0)
    registry = SubsystemHealthRegistry(clock=clock.now)

    registry.mark_starting("memory.persistence")
    clock.advance(2.0)
    degraded = registry.record_failure(
        "memory.persistence",
        OSError("disk unavailable"),
        kind=FailureKind.PERSISTENCE_FAILURE,
        degraded=True,
    )

    assert degraded.status == HealthStatus.DEGRADED
    assert degraded.consecutive_failures == 1
    assert degraded.total_failures == 1
    assert degraded.last_failure_at == 12.0

    clock.advance(3.0)
    healthy = registry.record_success("memory.persistence", details={"path": "state.json"})

    assert healthy.status == HealthStatus.HEALTHY
    assert healthy.consecutive_failures == 0
    assert healthy.total_failures == 1
    assert healthy.last_success_at == 15.0
    assert registry.snapshot()["overall"] == "healthy"


def test_boundary_records_failure_without_swallowing_exception() -> None:
    clock = FakeClock(100.0)
    registry = SubsystemHealthRegistry(clock=clock.now)

    with pytest.raises(TimeoutError):
        with registry.boundary(
            "provider.main",
            kind=FailureKind.PROVIDER_TRANSIENT,
            degraded=True,
        ):
            raise TimeoutError("provider timed out")

    item = registry.get("provider.main")
    assert item.status == HealthStatus.DEGRADED
    assert item.failure_kind == FailureKind.PROVIDER_TRANSIENT
    assert item.last_error_type == "TimeoutError"
