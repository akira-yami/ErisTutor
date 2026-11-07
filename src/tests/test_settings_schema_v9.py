from __future__ import annotations

import os

from runtime.policy_settings import EngagementSettings, RuntimeObservabilitySettings, RuntimePolicySettings
from runtime.runtime_settings_store import RuntimeSettingsStore, reset_runtime_settings_store_for_tests


def test_v8_settings_migrate_reliable_delivery_and_slo_defaults() -> None:
    migrated, changed = RuntimeSettingsStore._migrate_payload(
        {"version": 8, "engagement": {"event_consumer_batch_size": 8}}
    )
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["engagement"]["event_consumer_batch_size"] == 8
    assert migrated["engagement"]["delivery_lease_seconds"] == 30.0
    assert migrated["engagement"]["delivery_max_attempts"] == 5
    assert migrated["observability"]["minimum_ready_ratio"] == 0.95


def test_policy_settings_load_delivery_and_observability_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ERIS_ENGAGEMENT_DELIVERY_LEASE_SEC", "45")
    monkeypatch.setenv("ERIS_ENGAGEMENT_DELIVERY_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("ERIS_ENGAGEMENT_MAX_DEAD_LETTERS", "9")
    monkeypatch.setenv("ERIS_OBSERVABILITY_MIN_READY_RATIO", "0.88")
    monkeypatch.setenv("ERIS_OBSERVABILITY_MAX_DEGRADED_SEC", "42")
    monkeypatch.setenv("ERIS_RUNTIME_SETTINGS_PATH", str(tmp_path / "runtime_settings.json"))
    reset_runtime_settings_store_for_tests()

    engagement = EngagementSettings.load()
    observability = RuntimeObservabilitySettings.load()
    assert engagement.delivery_lease_seconds == 45.0
    assert engagement.delivery_max_attempts == 7
    assert engagement.max_dead_letters == 9
    assert observability.minimum_ready_ratio == 0.88
    assert observability.maximum_degraded_seconds == 42.0


def test_runtime_policy_settings_exports_observability_block() -> None:
    payload = RuntimePolicySettings.load().to_dict()
    assert "observability" in payload
    assert "minimum_ready_ratio" in payload["observability"]
    assert "delivery_lease_seconds" in payload["engagement"]
