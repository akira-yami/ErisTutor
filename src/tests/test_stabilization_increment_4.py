from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.bootstrap import shutdown_ai_orchestrated_runtime
from runtime.failure_taxonomy import FailureContext, classify_failure
from runtime.persistence_gate import PersistenceGate
from runtime.policy_settings import AIRuntimeSettings, RuntimePolicySettings
from runtime.runtime_settings_store import (
    RuntimeSettingsStore,
    get_runtime_settings_store,
    reset_runtime_settings_store_for_tests,
)
from runtime.settings_protocol import normalize_settings_message
from runtime.subsystem_health import FailureKind, HealthStatus, SubsystemHealthRegistry
from runtime.subsystem_supervisor import SubsystemSupervisor
from runtime.workspace_project_bundle import WorkspaceProjectBundle


def test_failure_taxonomy_uses_boundary_context() -> None:
    assert classify_failure(TimeoutError("slow"), context=FailureContext.PROVIDER) == FailureKind.PROVIDER_TRANSIENT
    assert classify_failure(OSError("disk"), context=FailureContext.PERSISTENCE) == FailureKind.PERSISTENCE_FAILURE
    assert classify_failure(ValueError("bad json"), context=FailureContext.EXTERNAL_DATA) == FailureKind.INVALID_EXTERNAL_DATA
    assert classify_failure(AssertionError("broken"), context=FailureContext.INVARIANT) == FailureKind.INVARIANT_VIOLATION
    assert classify_failure(TypeError("bug"), context=FailureContext.STARTUP) == FailureKind.PROGRAMMER_ERROR


def test_supervisor_classifies_startup_and_retries_failed_shutdown() -> None:
    health = SubsystemHealthRegistry()
    supervisor = SubsystemSupervisor(health_registry=health)

    missing = supervisor.start(
        "runtime.external",
        lambda: (_ for _ in ()).throw(ValueError("invalid workspace registry")),
        failure_context=FailureContext.EXTERNAL_DATA,
    )
    assert missing is None
    assert health.get("runtime.external").failure_kind == FailureKind.INVALID_EXTERNAL_DATA

    class FlakyService:
        attempts = 0

        def stop(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("worker still busy")

    service = supervisor.start("runtime.flaky", FlakyService, stop_methods=("stop",))
    assert service is not None
    assert supervisor.stop("runtime.flaky") is False
    assert supervisor.get("runtime.flaky") is service
    assert health.get("runtime.flaky").failure_kind == FailureKind.EXPECTED_DEGRADATION

    assert supervisor.stop("runtime.flaky") is True
    assert supervisor.get("runtime.flaky") is None
    assert health.get("runtime.flaky").status == HealthStatus.STOPPED


def test_persistence_gate_throttles_but_force_flushes() -> None:
    now = {"value": 10.0}
    gate = PersistenceGate(5.0, clock=lambda: now["value"])

    gate.mark_dirty()
    assert gate.should_flush() is True
    gate.mark_flushed()

    gate.mark_dirty()
    now["value"] = 12.0
    assert gate.should_flush() is False
    assert gate.should_flush(force=True) is True
    gate.mark_flushed()

    snapshot = gate.snapshot()
    assert snapshot.flush_count == 2
    assert snapshot.dirty is False
    assert snapshot.last_flush_at == 12.0


def test_ai_runtime_typed_settings_and_schema_v6(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SETTINGS_PATH", str(tmp_path / "runtime_settings.json"))
    reset_runtime_settings_store_for_tests()
    try:
        store = get_runtime_settings_store()
        result = store.apply_patch(
            {
                "ai_runtime": {
                    "observer_workers": 4,
                    "state_persistence_interval_seconds": 1.75,
                }
            }
        )
        assert result.rejected == ()
        settings = RuntimePolicySettings.load()
        assert settings.ai_runtime == AIRuntimeSettings(
            observer_workers=4,
            state_persistence_interval_seconds=1.75,
        )

        normalized = normalize_settings_message(
            {
                "scope": "runtime",
                "settings": {"ai_runtime": {"observer_workers": 3}},
            }
        )
        assert normalized.rejected_fields == ()
        assert "ai_runtime.observer_workers" in normalized.restart_required_paths
    finally:
        reset_runtime_settings_store_for_tests()

    migrated, changed = RuntimeSettingsStore._migrate_payload({"version": 5})
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["ai_runtime"]["observer_workers"] == 2
    assert migrated["ai_runtime"]["state_persistence_interval_seconds"] == 0.5


def test_ai_shutdown_forces_state_persistence_flush() -> None:
    health = SubsystemHealthRegistry()
    calls: list[tuple[dict, bool]] = []

    class Reducer:
        def snapshot(self):
            return SimpleNamespace(to_dict=lambda: {"version": 7})

        def close(self) -> None:
            return None

    def persist(payload, *, force=False) -> None:
        calls.append((dict(payload), bool(force)))

    host = SimpleNamespace(
        subsystem_health_registry=health,
        ai_state_reducer=Reducer(),
        _persist_ai_runtime_state=persist,
        ai_engagement_runtime=None,
    )

    shutdown_ai_orchestrated_runtime(host)

    assert calls == [({"version": 7}, True)]
    assert health.get("ai_state.persistence").status == HealthStatus.HEALTHY
    assert health.get("ai_orchestrated.runtime").status == HealthStatus.STOPPED


def test_workspace_project_bundle_binds_and_unbinds_atomically(tmp_path) -> None:
    class Executor:
        def __init__(self) -> None:
            self.project_runtime = "sentinel"

        def bind_project_runtime(self, runtime) -> None:
            self.project_runtime = runtime

    executor = Executor()
    owner = SimpleNamespace(
        action_executor=executor,
        runtime_settings={
            "project_ops": {
                "eris_home": str(tmp_path / "eris-home"),
                "workspace_root": str(tmp_path / "workspaces"),
                "enabled": True,
            }
        },
        workspace_manager=None,
        project_operations=None,
    )

    bundle = WorkspaceProjectBundle.build(owner)
    snapshot = bundle.snapshot()

    assert owner.workspace_manager is bundle.workspace_manager
    assert owner.project_operations is bundle.project_operations
    assert executor.project_runtime is bundle.project_operations
    assert snapshot["capabilities"] > 0
    assert snapshot["closed"] is False

    bundle.close()
    assert owner.workspace_manager is None
    assert owner.project_operations is None
    assert executor.project_runtime is None
    assert bundle.snapshot()["closed"] is True
