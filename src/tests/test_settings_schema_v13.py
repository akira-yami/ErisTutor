from __future__ import annotations

from runtime.runtime_settings_store import RuntimeSettingsStore


def test_v12_settings_migrate_companion_routing_defaults() -> None:
    migrated, changed = RuntimeSettingsStore._migrate_payload(
        {"version": 12, "chat": {"language": "vi"}}
    )
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["architecture"]["inner_diary_model_enabled"] is True
    assert migrated["architecture"]["private_residue_curator_enabled"] is True
    assert migrated["chat"]["companion_routing_enabled"] is True
    assert migrated["chat"]["companion_pro_threshold"] == 0.58
    assert migrated["chat"]["companion_style_uses_pro"] is True
