from __future__ import annotations

from runtime.loop_guard import RuntimeLoopGuard
from runtime.policy_settings import ProtocolSettings, RunLoopSettings, RuntimePolicySettings
from runtime.runtime_settings_store import (
    RuntimeSettingsStore,
    get_runtime_settings_store,
    reset_runtime_settings_store_for_tests,
)
from runtime.settings_protocol import normalize_settings_message
from runtime.subsystem_health import HealthStatus, SubsystemHealthRegistry


def test_loop_guard_backoff_caps_recovers_and_can_open_circuit() -> None:
    health = SubsystemHealthRegistry()
    settings = RunLoopSettings(
        startup_retry_delay_seconds=0.1,
        error_backoff_initial_seconds=0.25,
        error_backoff_max_seconds=1.0,
        consecutive_error_stop_limit=3,
    )
    guard = RuntimeLoopGuard(settings=settings, health_registry=health)

    assert guard.record_failure(RuntimeError("one")) == 0.25
    assert guard.should_stop() is False
    assert guard.record_failure(RuntimeError("two")) == 0.5
    assert guard.record_failure(RuntimeError("three")) == 1.0
    assert guard.should_stop() is True
    assert health.get("runtime.autonomous_loop").status == HealthStatus.DEGRADED

    guard.record_success()
    assert guard.consecutive_errors == 0
    assert health.get("runtime.autonomous_loop").status == HealthStatus.HEALTHY


def test_schema_v7_migrates_protocol_and_loop_defaults() -> None:
    migrated, changed = RuntimeSettingsStore._migrate_payload({"version": 6})
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["protocol"] == {
        "max_message_type_chars": 96,
        "reject_non_mapping": True,
        "record_unknown_messages": True,
    }
    assert migrated["run_loop"]["error_backoff_initial_seconds"] == 0.25
    assert migrated["run_loop"]["consecutive_error_stop_limit"] == 0


def test_protocol_and_loop_settings_are_accepted_live(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SETTINGS_PATH", str(tmp_path / "runtime_settings.json"))
    reset_runtime_settings_store_for_tests()
    try:
        store = get_runtime_settings_store()
        result = store.apply_patch(
            {
                "protocol": {
                    "max_message_type_chars": 144,
                    "record_unknown_messages": False,
                },
                "run_loop": {
                    "error_backoff_initial_seconds": 0.5,
                    "error_backoff_max_seconds": 3.0,
                    "consecutive_error_stop_limit": 8,
                },
            }
        )
        assert result.rejected == ()
        settings = RuntimePolicySettings.load()
        assert settings.protocol == ProtocolSettings(
            max_message_type_chars=144,
            reject_non_mapping=True,
            record_unknown_messages=False,
        )
        assert settings.run_loop.error_backoff_initial_seconds == 0.5
        assert settings.run_loop.error_backoff_max_seconds == 3.0
        assert settings.run_loop.consecutive_error_stop_limit == 8

        normalized = normalize_settings_message(
            {
                "scope": "runtime",
                "settings": {
                    "protocol": {"max_message_type_chars": 120},
                    "run_loop": {"consecutive_error_stop_limit": 4},
                },
            }
        )
        assert normalized.rejected_fields == ()
        assert set(normalized.patch) == {"protocol", "run_loop"}
    finally:
        reset_runtime_settings_store_for_tests()
