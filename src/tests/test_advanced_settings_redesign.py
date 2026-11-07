# -*- coding: utf-8 -*-
"""Regression cho Advanced Settings mới.

Các test này giữ ba contract dễ bị biến lại thành UI trang trí:
- presence/commentary/discovery được protocol chấp nhận;
- Brain persist và trả metadata thật;
- quyền Chỉ đọc chặn capability ghi file thật sự.
"""

from __future__ import annotations


def test_advanced_protocol_accepts_behavior_and_project_roots() -> None:
    from runtime.settings_protocol import normalize_settings_message

    normalized = normalize_settings_message({
        "scope": "advanced",
        "settings": {
            "commentary": {"enabled": True, "desired_interval_seconds": 60},
            "initiative": {"enabled": True, "global_hourly_budget": 40},
            "discovery": {"enabled": False, "local_signal_enabled": False},
            "memory": {"background_digest": True, "maintenance_enabled": True},
            "tutor": {"self_search_enabled": False},
            "project_ops": {"enabled": True, "autonomy_mode": "guarded"},
        },
    })

    assert normalized.scope == "advanced"
    assert normalized.rejected_fields == ()
    assert set(normalized.patch) == {
        "commentary", "initiative", "discovery", "memory", "tutor", "project_ops"
    }


def test_runtime_store_applies_new_advanced_fields_and_reports_metadata(tmp_path) -> None:
    from runtime.runtime_settings_store import RuntimeSettingsStore

    store = RuntimeSettingsStore(tmp_path / "runtime_settings.json")
    result = store.apply_patch({
        "commentary": {
            "enabled": True,
            "desired_interval_seconds": 35,
            "maximum_quiet_seconds": 75,
            "autonomous_expression_enabled": True,
        },
        "project_ops": {
            "autonomy_mode": "read_only",
            "max_repair_attempts": 3,
        },
        "tutor": {"self_search_enabled": False},
    })

    assert result.rejected == ()
    assert result.settings["commentary"]["desired_interval_seconds"] == 35
    assert result.settings["project_ops"]["autonomy_mode"] == "read_only"
    assert result.settings["project_ops"]["max_repair_attempts"] == 3
    assert result.settings["tutor"]["self_search_enabled"] is False

    metadata = store.metadata()
    assert metadata["revision"] >= 1
    assert metadata["saved_at"] > 0
    assert metadata["path"].endswith("runtime_settings.json")


def test_project_read_only_mode_blocks_mutating_capabilities() -> None:
    from runtime.project_operations import project_autonomy_allows

    assert project_autonomy_allows("project_read_file", "read_only") == (
        True, "project_autonomy_allowed"
    )
    assert project_autonomy_allows("project_git_diff", "read_only") == (
        True, "project_autonomy_allowed"
    )
    assert project_autonomy_allows("project_apply_patch", "read_only") == (
        False, "project_read_only_mode"
    )
    assert project_autonomy_allows("project_execute_task", "read_only") == (
        False, "project_read_only_mode"
    )


def test_tutor_does_not_self_search_by_default() -> None:
    from runtime.runtime_settings_store import RuntimeSettingsStore

    defaults = RuntimeSettingsStore.defaults()
    assert defaults["tutor"]["enabled"] is False
    assert defaults["tutor"]["self_search_enabled"] is False
