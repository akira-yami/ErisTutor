from __future__ import annotations

from runtime.policy_settings import EngagementSettings
from runtime.runtime_settings_store import RuntimeSettingsStore, reset_runtime_settings_store_for_tests


def test_v9_settings_migrate_gap_and_pressure_defaults() -> None:
    migrated, changed = RuntimeSettingsStore._migrate_payload(
        {"version": 9, "engagement": {"delivery_max_attempts": 7}}
    )
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["engagement"]["delivery_max_attempts"] == 7
    assert migrated["engagement"]["delivery_stop_on_gap"] is True
    assert migrated["engagement"]["consumer_lag_warning_events"] == 128
    assert migrated["engagement"]["open_dead_letter_warning_count"] == 1


def test_v10_engagement_settings_load_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setenv("ERIS_ENGAGEMENT_STOP_ON_GAP", "false")
    monkeypatch.setenv("ERIS_ENGAGEMENT_LAG_WARNING_EVENTS", "77")
    monkeypatch.setenv("ERIS_ENGAGEMENT_OPEN_DLQ_WARNING", "3")
    reset_runtime_settings_store_for_tests()
    try:
        settings = EngagementSettings.load()
        assert settings.delivery_stop_on_gap is False
        assert settings.consumer_lag_warning_events == 77
        assert settings.open_dead_letter_warning_count == 3
    finally:
        reset_runtime_settings_store_for_tests()


def test_v10_settings_protocol_marks_recovery_policy_for_restart() -> None:
    from runtime.settings_protocol import normalize_settings_message

    normalized = normalize_settings_message({
        "scope": "runtime",
        "settings": {
            "engagement": {
                "delivery_stop_on_gap": False,
                "consumer_lag_warning_events": 50,
                "open_dead_letter_warning_count": 2,
            }
        },
    })
    assert normalized.rejected_fields == ()
    assert "engagement.delivery_stop_on_gap" in normalized.restart_required_paths
    assert "engagement.consumer_lag_warning_events" in normalized.restart_required_paths
    assert "engagement.open_dead_letter_warning_count" in normalized.restart_required_paths
