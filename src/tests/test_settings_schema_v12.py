from __future__ import annotations

from types import SimpleNamespace

from runtime.policy_settings import RuntimePolicySettings
from runtime.runtime_diagnostics import RuntimeDiagnostics
from runtime.runtime_settings_store import (
    RuntimeSettingsStore,
    get_runtime_settings_store,
    reset_runtime_settings_store_for_tests,
)
from runtime.settings_protocol import normalize_settings_message


def test_v11_settings_migrate_snapshot_journal_defaults():
    migrated, changed = RuntimeSettingsStore._migrate_payload(
        {"version": 11, "engagement": {"max_events": 200}}
    )
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["engagement"]["snapshot_journal_enabled"] is True
    assert migrated["engagement"]["snapshot_journal_auto_repair"] is True


def test_typed_settings_load_snapshot_journal_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_RUNTIME_SETTINGS_PATH", str(tmp_path / "settings.json"))
    reset_runtime_settings_store_for_tests()
    try:
        store = get_runtime_settings_store()
        result = store.apply_patch(
            {"engagement": {"snapshot_journal_enabled": True, "snapshot_journal_auto_repair": False}}
        )
        assert result.rejected == ()
        settings = RuntimePolicySettings.load().engagement
        assert settings.snapshot_journal_enabled is True
        assert settings.snapshot_journal_auto_repair is False
    finally:
        reset_runtime_settings_store_for_tests()


def test_settings_protocol_marks_snapshot_journal_changes_for_restart():
    normalized = normalize_settings_message(
        {
            "scope": "runtime",
            "settings": {
                "engagement": {
                    "snapshot_journal_enabled": False,
                    "snapshot_journal_auto_repair": False,
                }
            },
        }
    )
    assert normalized.rejected_fields == ()
    assert "engagement.snapshot_journal_enabled" in normalized.restart_required_paths
    assert "engagement.snapshot_journal_auto_repair" in normalized.restart_required_paths


def test_runtime_diagnostics_projects_snapshot_journal_state():
    owner = SimpleNamespace(
        bootstrap_completed_phases=("a", "b", "c", "d", "e", "f"),
        ai_runtime_enabled=True,
        subsystem_health_registry=None,
        subsystem_supervisor=None,
        transport_boundary=None,
        incoming_dispatcher=None,
        loop_guard=None,
        ai_engagement_runtime=None,
        _ai_engagement_snapshot_journal=SimpleNamespace(
            diagnostics=lambda: {
                "schema_version": 1,
                "has_snapshots": True,
                "primary_valid": True,
                "backup_valid": True,
            }
        ),
    )
    snapshot = RuntimeDiagnostics(owner, clock=lambda: 1.0).snapshot()
    assert snapshot["schema_version"] == 7
    assert snapshot["engagement"]["snapshot_journal"]["primary_valid"] is True
