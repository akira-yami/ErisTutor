from __future__ import annotations

from types import SimpleNamespace

from runtime.policy_settings import (
    EngagementSettings,
    PublicTurnSettings,
    RuntimePolicySettings,
)
from runtime.public_turn_ledger import PublicTurnLedger
from runtime.public_turn_runtime import PublicTurnRuntime
from runtime.runtime_settings_store import (
    get_runtime_settings_store,
    reset_runtime_settings_store_for_tests,
)
from runtime.settings_protocol import normalize_settings_message
from runtime.subsystem_health import HealthStatus, SubsystemHealthRegistry
from runtime.subsystem_supervisor import SubsystemSupervisor


def test_typed_policy_settings_load_from_authoritative_store(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SETTINGS_PATH", str(tmp_path / "runtime_settings.json"))
    reset_runtime_settings_store_for_tests()
    try:
        store = get_runtime_settings_store()
        result = store.apply_patch(
            {
                "public_turn": {
                    "reply_window_seconds": 42,
                    "collision_grace_seconds": 3.0,
                },
                "engagement": {
                    "enabled": True,
                    "max_events": 240,
                    "persistence_interval_seconds": 1.25,
                    "signal_importance_threshold": 0.72,
                    "activity_key_cache_size": 300,
                    "activity_key_retention": 140,
                },
                "health": {"max_events": 300},
            }
        )
        assert result.rejected == ()

        settings = RuntimePolicySettings.load()
        assert settings.public_turn.reply_window_seconds == 42.0
        assert settings.public_turn.collision_grace_seconds == 3.0
        assert settings.engagement == EngagementSettings(
            enabled=True,
            max_events=240,
            persistence_interval_seconds=1.25,
            signal_importance_threshold=0.72,
            activity_key_cache_size=300,
            activity_key_retention=140,
        )
        assert settings.health.max_events == 300
    finally:
        reset_runtime_settings_store_for_tests()


def test_public_turn_runtime_uses_injected_typed_policy() -> None:
    now = {"value": 100.0}
    owner = SimpleNamespace(
        public_turn_ledger=PublicTurnLedger(),
        reply_context=SimpleNamespace(previous_public_turn=None),
        chat_mode=True,
        _chat_processing=False,
        autonomous_turn_collision_until=0.0,
    )
    runtime = PublicTurnRuntime(
        owner,
        clock=lambda: now["value"],
        settings=PublicTurnSettings(
            reply_window_seconds=10.0,
            collision_grace_seconds=2.5,
        ),
    )
    decision = SimpleNamespace(
        text="Public turn",
        should_speak=True,
        parse_mode="json",
        intent="reply",
        reasoning_tags=(),
    )
    turn = runtime.record(decision)
    assert turn is not None

    assert runtime.link_previous_for_chat(109.0) is turn
    assert runtime.link_previous_for_chat(111.0) is None

    runtime.note_turn_collision(now=111.0, seconds=1000.0)
    # Collision là boundary cơ học rất ngắn, không phải social cooldown.
    assert owner.autonomous_turn_collision_until == 116.0


def test_settings_protocol_accepts_policy_roots_and_marks_restart() -> None:
    normalized = normalize_settings_message(
        {
            "scope": "runtime",
            "settings": {
                "public_turn": {"reply_window_seconds": 45},
                "engagement": {"signal_importance_threshold": 0.6},
                "health": {"max_events": 256},
            },
        }
    )

    assert normalized.rejected_fields == ()
    assert set(normalized.patch) == {"public_turn", "engagement", "health"}
    assert "public_turn.reply_window_seconds" in normalized.restart_required_paths
    assert "engagement.signal_importance_threshold" in normalized.restart_required_paths
    assert "health.max_events" in normalized.restart_required_paths


def test_subsystem_supervisor_records_failure_and_stops_reverse_order() -> None:
    health = SubsystemHealthRegistry()
    supervisor = SubsystemSupervisor(health_registry=health)
    stopped: list[str] = []

    class Service:
        def __init__(self, name: str) -> None:
            self.name = name

        def stop(self) -> None:
            stopped.append(self.name)

    first = supervisor.start("runtime.first", lambda: Service("first"), stop_methods=("stop",))
    second = supervisor.start("runtime.second", lambda: Service("second"), stop_methods=("stop",))
    missing = supervisor.start("runtime.broken", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert first is not None and second is not None
    assert missing is None
    assert health.get("runtime.broken").status == HealthStatus.DEGRADED

    assert supervisor.shutdown_all() == ("runtime.second", "runtime.first")
    assert stopped == ["second", "first"]
    assert health.get("runtime.first").status == HealthStatus.STOPPED
    assert health.get("runtime.second").status == HealthStatus.STOPPED


def test_engagement_signal_threshold_is_injected_not_hard_coded() -> None:
    from runtime.engagements.adapters import AdapterRegistry
    from runtime.engagements.contracts import InteractionSignal
    from runtime.engagements.detector import EngagementDetector
    from runtime.engagements.policy import EngagementInteractionPolicy
    from runtime.engagements.registry import EngagementRegistry

    proposal = EngagementDetector().propose("hãy theo dõi deployment này")
    assert proposal is not None
    engagement = EngagementRegistry(AdapterRegistry(), clock=lambda: 0.0).create(proposal, now=0.0)
    signal = InteractionSignal(
        engagement_id=engagement.engagement_id,
        kind="deployment.completed",
        importance=0.6,
        reason="deployment completed",
    )

    strict = EngagementInteractionPolicy(signal_importance_threshold=0.7).evaluate(
        engagement,
        (signal,),
        now=20.0,
        seconds_since_user=20.0,
        seconds_since_assistant=20.0,
    )
    permissive = EngagementInteractionPolicy(signal_importance_threshold=0.5).evaluate(
        engagement,
        (signal,),
        now=20.0,
        seconds_since_user=20.0,
        seconds_since_assistant=20.0,
    )

    assert strict.due is False
    assert permissive.due is True


def test_runtime_settings_v4_migrates_typed_policy_blocks() -> None:
    from runtime.runtime_settings_store import RuntimeSettingsStore

    migrated, changed = RuntimeSettingsStore._migrate_payload(
        {
            "version": 4,
            "commentary": {"enabled": True},
            "initiative": {"global_hourly_budget": 40},
        }
    )

    assert changed is True
    assert migrated["version"] == 18
    assert migrated["public_turn"]["reply_window_seconds"] == 30.0
    assert migrated["public_turn"]["collision_grace_seconds"] == 2.5
    assert "default_block_seconds" not in migrated["public_turn"]
    assert migrated["engagement"]["max_events"] == 160
    assert migrated["health"]["max_events"] == 128
    assert migrated["engagement"]["max_terminal_engagements"] == 64
    assert migrated["engagement"]["terminal_retention_seconds"] == 604800.0
    assert migrated["engagement"]["event_consumer_batch_size"] == 128


def test_v66_expression_is_open_by_default_not_legacy_opt_in() -> None:
    from runtime.runtime_settings_store import RuntimeSettingsStore

    defaults = RuntimeSettingsStore.defaults()
    assert defaults["commentary"]["autonomous_expression_enabled"] is True
    assert "user_requested_autonomous_surface" not in defaults["commentary"]
    assert "minimum_user_silence_seconds" not in defaults["commentary"]
    assert "minimum_user_silence_seconds" not in defaults["initiative"]


def test_v66_migration_distinguishes_old_default_from_explicit_quiet_boundary() -> None:
    from runtime.runtime_settings_store import RuntimeSettingsStore

    old_default, changed_default = RuntimeSettingsStore._migrate_payload({
        "version": 16,
        "commentary": {
            "enabled": False,
            "user_requested_autonomous_surface": False,
            "preference_updated_at": 0,
            "minimum_user_silence_seconds": 12,
        },
        "initiative": {"enabled": False, "minimum_user_silence_seconds": 12},
    })
    assert changed_default is True
    assert old_default["commentary"]["autonomous_expression_enabled"] is True
    assert "user_requested_autonomous_surface" not in old_default["commentary"]

    explicit_quiet, changed_quiet = RuntimeSettingsStore._migrate_payload({
        "version": 16,
        "commentary": {
            "enabled": False,
            "user_requested_autonomous_surface": False,
            "preference_updated_at": 123456,
        },
        "initiative": {"enabled": False},
    })
    assert changed_quiet is True
    assert explicit_quiet["commentary"]["autonomous_expression_enabled"] is False
    # Boundary chỉ chặn public expression; cognition/initiative vẫn sống.
    assert explicit_quiet["commentary"]["enabled"] is True
    assert explicit_quiet["initiative"]["enabled"] is True
