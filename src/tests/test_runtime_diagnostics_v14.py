from __future__ import annotations

from types import SimpleNamespace

from runtime.runtime_diagnostics import RuntimeDiagnostics


class Snapshotter:
    def __init__(self, payload):
        self.payload = payload

    def snapshot(self, **kwargs):
        return {**self.payload, "kwargs": kwargs}


class BrokenSnapshotter:
    def snapshot(self, **kwargs):
        raise RuntimeError("snapshot unavailable")


class NamedRegistry:
    def ids(self):
        return ("alpha", "beta")


def test_runtime_diagnostics_unifies_boundaries_and_engagement() -> None:
    engagement = SimpleNamespace(
        registry=Snapshotter({"revision": 4}),
        profiles=NamedRegistry(),
        adapters=NamedRegistry(),
        planner_context=lambda now: {"active": True, "now": now},
    )
    owner = SimpleNamespace(
        bootstrap_completed_phases=("foundation", "memory"),
        ai_runtime_enabled=True,
        subsystem_health_registry=Snapshotter({"overall": "healthy"}),
        subsystem_supervisor=Snapshotter({"services": 3}),
        transport_boundary=Snapshotter({"sent": 9}),
        incoming_dispatcher=Snapshotter({"messages": 5}),
        loop_guard=Snapshotter({"iterations": 8}),
        ai_engagement_runtime=engagement,
    )

    snapshot = RuntimeDiagnostics(owner, clock=lambda: 123.0).snapshot(include_events=False)

    assert snapshot["generated_at"] == 123.0
    assert snapshot["bootstrap"]["completed_phases"] == ["foundation", "memory"]
    assert snapshot["boundaries"]["transport"]["sent"] == 9
    assert snapshot["engagement"]["profiles"] == ["alpha", "beta"]
    assert snapshot["engagement"]["planner"]["active"] is True


def test_runtime_diagnostics_contains_boundary_errors_instead_of_raising() -> None:
    owner = SimpleNamespace(
        bootstrap_completed_phases=(),
        ai_runtime_enabled=False,
        subsystem_health_registry=BrokenSnapshotter(),
        subsystem_supervisor=None,
        transport_boundary=BrokenSnapshotter(),
        incoming_dispatcher=None,
        loop_guard=None,
        ai_engagement_runtime=None,
    )

    snapshot = RuntimeDiagnostics(owner, clock=lambda: 1.0).snapshot()

    assert snapshot["health"]["error"] == "RuntimeError"
    assert snapshot["boundaries"]["transport"]["message"] == "snapshot unavailable"
