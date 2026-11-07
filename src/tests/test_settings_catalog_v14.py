from __future__ import annotations

from runtime.runtime_settings_store import RuntimeSettingsStore
from runtime.settings_catalog import build_settings_catalog
from runtime.settings_protocol import normalize_settings_message


def _default_paths() -> set[str]:
    defaults = RuntimeSettingsStore.defaults()
    return {
        f"{section}.{key}"
        for section, values in defaults.items()
        if section != "version" and isinstance(values, dict)
        for key in values
    }


def test_catalog_covers_every_runtime_setting_without_secrets() -> None:
    defaults = RuntimeSettingsStore.defaults()
    catalog = build_settings_catalog(defaults)
    fields = {item["path"]: item for item in catalog["fields"]}

    assert catalog["schema_version"] == 18
    assert set(fields) == _default_paths()
    assert len(catalog["sections"]) == 25
    assert all(item["owner"] == "brain_runtime" for item in fields.values())
    assert fields["tts.provider"]["surface"] == "voice"
    assert fields["chat.reasoning_effort"]["surface"] == "advanced"
    assert fields["tts.provider"]["options"] == ["elevenlabs", "gemini"]
    assert fields["engagement.max_events"]["min"] == 24
    assert fields["engagement.max_events"]["max"] == 4096
    assert all(not path.startswith("opening_director.") for path in fields)
    rendered_fields = repr(catalog["fields"]).lower()
    assert "api_key" not in rendered_fields
    assert "secret" not in rendered_fields


def test_opening_director_settings_are_retired() -> None:
    assert "opening_director" not in RuntimeSettingsStore.defaults()
    migrated, changed = RuntimeSettingsStore._migrate_payload({
        "version": 18,
        "opening_director": {"enabled": True},
    })
    assert changed is True
    assert "opening_director" not in migrated


def test_scope_ownership_rejects_cross_surface_writes() -> None:
    advanced = normalize_settings_message({
        "scope": "advanced",
        "settings": {
            "chat": {"reasoning_effort": "high"},
            "danger_zone": {"maintenance_enabled": False},
            "tts": {"provider": "gemini"},
        },
    })
    assert advanced.patch["chat"]["reasoning_effort"] == "high"
    assert advanced.patch["danger_zone"]["maintenance_enabled"] is False
    assert "tts" not in advanced.patch
    assert "tts" in advanced.rejected_fields

    voice = normalize_settings_message({
        "scope": "tts",
        "settings": {
            "tts": {
                "provider": "elevenlabs",
                "elevenlabs_model": "eleven_v3",
            },
            "chat": {"reasoning_effort": "low"},
        },
    })
    assert voice.patch == {
        "tts": {
            "provider": "elevenlabs",
            "elevenlabs_model": "eleven_v3",
        }
    }
    assert "chat" in voice.rejected_fields
