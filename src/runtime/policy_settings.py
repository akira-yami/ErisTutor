# -*- coding: utf-8 -*-
"""Typed runtime settings for continuity, health and long-running engagements.

The JSON settings store remains the authority.  These dataclasses are the
boundary objects consumed by runtime services so policy code does not scatter
string paths and ad-hoc clamping across the repository.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from runtime.runtime_settings_store import runtime_bool, runtime_float, runtime_int


def _clamp_float(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        parsed = float(default)
    return max(float(minimum), min(float(maximum), parsed))


def _clamp_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


@dataclass(frozen=True, slots=True)
class AIRuntimeSettings:
    observer_workers: int = 2
    state_persistence_interval_seconds: float = 0.5

    @classmethod
    def load(cls) -> "AIRuntimeSettings":
        return cls(
            observer_workers=_clamp_int(
                runtime_int("ai_runtime", "observer_workers", default=2),
                2,
                minimum=1,
                maximum=8,
            ),
            state_persistence_interval_seconds=_clamp_float(
                runtime_float(
                    "ai_runtime",
                    "state_persistence_interval_seconds",
                    default=0.5,
                ),
                0.5,
                minimum=0.0,
                maximum=60.0,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PublicTurnSettings:
    reply_window_seconds: float = 30.0
    # V66: đây chỉ là khoảng đệm chống đè đúng *lượt phát ngôn*, không phải
    # social cooldown và tuyệt đối không được dùng để ngủ cả cognition loop.
    collision_grace_seconds: float = 2.5

    @classmethod
    def load(cls) -> "PublicTurnSettings":
        return cls(
            reply_window_seconds=_clamp_float(
                runtime_float("public_turn", "reply_window_seconds", default=30.0),
                30.0,
                minimum=5.0,
                maximum=300.0,
            ),
            collision_grace_seconds=_clamp_float(
                runtime_float("public_turn", "collision_grace_seconds", default=2.5),
                2.5,
                minimum=0.0,
                maximum=5.0,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EngagementSettings:
    enabled: bool = True
    max_events: int = 160
    persistence_interval_seconds: float = 2.0
    signal_importance_threshold: float = 0.45
    activity_key_cache_size: int = 256
    activity_key_retention: int = 128
    max_terminal_engagements: int = 64
    terminal_retention_seconds: float = 604800.0
    event_consumer_batch_size: int = 128
    delivery_lease_seconds: float = 30.0
    delivery_max_attempts: int = 5
    delivery_backoff_initial_seconds: float = 2.0
    delivery_backoff_max_seconds: float = 60.0
    max_dead_letters: int = 64
    delivery_stop_on_gap: bool = True
    consumer_lag_warning_events: int = 128
    open_dead_letter_warning_count: int = 1
    max_delivery_receipts: int = 512
    delivery_split_poison_batches: bool = False
    delivery_split_after_attempts: int = 2
    managed_consumer_poll_budget: int = 8
    snapshot_journal_enabled: bool = True
    snapshot_journal_auto_repair: bool = True

    @classmethod
    def load(cls) -> "EngagementSettings":
        cache_size = _clamp_int(
            runtime_int("engagement", "activity_key_cache_size", default=256),
            256,
            minimum=32,
            maximum=4096,
        )
        retention = _clamp_int(
            runtime_int("engagement", "activity_key_retention", default=128),
            128,
            minimum=16,
            maximum=cache_size,
        )
        return cls(
            enabled=runtime_bool("engagement", "enabled", default=True),
            max_events=_clamp_int(
                runtime_int("engagement", "max_events", default=160),
                160,
                minimum=24,
                maximum=4096,
            ),
            persistence_interval_seconds=_clamp_float(
                runtime_float("engagement", "persistence_interval_seconds", default=2.0),
                2.0,
                minimum=0.0,
                maximum=60.0,
            ),
            signal_importance_threshold=_clamp_float(
                runtime_float("engagement", "signal_importance_threshold", default=0.45),
                0.45,
                minimum=0.0,
                maximum=1.0,
            ),
            activity_key_cache_size=cache_size,
            activity_key_retention=retention,
            max_terminal_engagements=_clamp_int(
                runtime_int("engagement", "max_terminal_engagements", default=64),
                64,
                minimum=0,
                maximum=4096,
            ),
            terminal_retention_seconds=_clamp_float(
                runtime_float("engagement", "terminal_retention_seconds", default=604800.0),
                604800.0,
                minimum=0.0,
                maximum=31536000.0,
            ),
            event_consumer_batch_size=_clamp_int(
                runtime_int("engagement", "event_consumer_batch_size", default=128),
                128,
                minimum=1,
                maximum=1024,
            ),
            delivery_lease_seconds=_clamp_float(
                runtime_float("engagement", "delivery_lease_seconds", default=30.0),
                30.0, minimum=1.0, maximum=3600.0,
            ),
            delivery_max_attempts=_clamp_int(
                runtime_int("engagement", "delivery_max_attempts", default=5),
                5, minimum=1, maximum=100,
            ),
            delivery_backoff_initial_seconds=_clamp_float(
                runtime_float("engagement", "delivery_backoff_initial_seconds", default=2.0),
                2.0, minimum=0.0, maximum=3600.0,
            ),
            delivery_backoff_max_seconds=_clamp_float(
                runtime_float("engagement", "delivery_backoff_max_seconds", default=60.0),
                60.0, minimum=0.0, maximum=86400.0,
            ),
            max_dead_letters=_clamp_int(
                runtime_int("engagement", "max_dead_letters", default=64),
                64, minimum=1, maximum=4096,
            ),
            delivery_stop_on_gap=runtime_bool(
                "engagement", "delivery_stop_on_gap", default=True
            ),
            consumer_lag_warning_events=_clamp_int(
                runtime_int("engagement", "consumer_lag_warning_events", default=128),
                128, minimum=1, maximum=1000000,
            ),
            open_dead_letter_warning_count=_clamp_int(
                runtime_int("engagement", "open_dead_letter_warning_count", default=1),
                1, minimum=1, maximum=4096,
            ),
            max_delivery_receipts=_clamp_int(
                runtime_int("engagement", "max_delivery_receipts", default=512),
                512, minimum=32, maximum=16384,
            ),
            delivery_split_poison_batches=runtime_bool(
                "engagement", "delivery_split_poison_batches", default=True
            ),
            delivery_split_after_attempts=_clamp_int(
                runtime_int("engagement", "delivery_split_after_attempts", default=2),
                2, minimum=1, maximum=100,
            ),
            managed_consumer_poll_budget=_clamp_int(
                runtime_int("engagement", "managed_consumer_poll_budget", default=8),
                8, minimum=1, maximum=256,
            ),
            snapshot_journal_enabled=runtime_bool(
                "engagement", "snapshot_journal_enabled", default=True
            ),
            snapshot_journal_auto_repair=runtime_bool(
                "engagement", "snapshot_journal_auto_repair", default=True
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)




@dataclass(frozen=True, slots=True)
class ChessBridgeSettings:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    analysis_depth: int = 2
    direct_move_rate: float = 0.18
    stockfish_enabled: bool = True
    stockfish_path: str = ""
    stockfish_threads: int = 1
    stockfish_hash_mb: int = 96
    stockfish_multipv: int = 3
    stockfish_movetime_ms: int = 180
    stockfish_on_demand_movetime_ms: int = 700
    stockfish_startup_timeout_seconds: float = 4.0
    stockfish_analysis_timeout_seconds: float = 2.5
    max_body_bytes: int = 65536

    @classmethod
    def load(cls) -> "ChessBridgeSettings":
        from runtime.runtime_settings_store import runtime_text
        return cls(
            enabled=runtime_bool("chess_bridge", "enabled", default=True),
            host=str(runtime_text("chess_bridge", "host", default="127.0.0.1") or "127.0.0.1")[:128],
            port=_clamp_int(
                runtime_int("chess_bridge", "port", default=8765),
                8765, minimum=1024, maximum=65535,
            ),
            analysis_depth=_clamp_int(
                runtime_int("chess_bridge", "analysis_depth", default=2),
                2, minimum=1, maximum=4,
            ),
            direct_move_rate=_clamp_float(
                runtime_float("chess_bridge", "direct_move_rate", default=0.18),
                0.18, minimum=0.0, maximum=1.0,
            ),
            stockfish_enabled=runtime_bool("chess_bridge", "stockfish_enabled", default=True),
            stockfish_path=str(runtime_text("chess_bridge", "stockfish_path", default="") or "")[:1024],
            stockfish_threads=_clamp_int(
                runtime_int("chess_bridge", "stockfish_threads", default=1),
                1, minimum=1, maximum=32,
            ),
            stockfish_hash_mb=_clamp_int(
                runtime_int("chess_bridge", "stockfish_hash_mb", default=96),
                96, minimum=16, maximum=4096,
            ),
            stockfish_multipv=_clamp_int(
                runtime_int("chess_bridge", "stockfish_multipv", default=3),
                3, minimum=1, maximum=8,
            ),
            stockfish_movetime_ms=_clamp_int(
                runtime_int("chess_bridge", "stockfish_movetime_ms", default=180),
                180, minimum=25, maximum=10000,
            ),
            stockfish_on_demand_movetime_ms=_clamp_int(
                runtime_int("chess_bridge", "stockfish_on_demand_movetime_ms", default=700),
                700, minimum=50, maximum=30000,
            ),
            stockfish_startup_timeout_seconds=_clamp_float(
                runtime_float("chess_bridge", "stockfish_startup_timeout_seconds", default=4.0),
                4.0, minimum=0.5, maximum=30.0,
            ),
            stockfish_analysis_timeout_seconds=_clamp_float(
                runtime_float("chess_bridge", "stockfish_analysis_timeout_seconds", default=2.5),
                2.5, minimum=0.5, maximum=60.0,
            ),
            max_body_bytes=_clamp_int(
                runtime_int("chess_bridge", "max_body_bytes", default=65536),
                65536, minimum=4096, maximum=1048576,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HealthSettings:
    max_events: int = 128

    @classmethod
    def load(cls) -> "HealthSettings":
        return cls(
            max_events=_clamp_int(
                runtime_int("health", "max_events", default=128),
                128,
                minimum=16,
                maximum=4096,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)




@dataclass(frozen=True, slots=True)
class ProtocolSettings:
    max_message_type_chars: int = 96
    reject_non_mapping: bool = True
    record_unknown_messages: bool = True

    @classmethod
    def load(cls) -> "ProtocolSettings":
        return cls(
            max_message_type_chars=_clamp_int(
                runtime_int("protocol", "max_message_type_chars", default=96),
                96, minimum=16, maximum=512,
            ),
            reject_non_mapping=runtime_bool("protocol", "reject_non_mapping", default=True),
            record_unknown_messages=runtime_bool("protocol", "record_unknown_messages", default=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunLoopSettings:
    startup_retry_delay_seconds: float = 0.2
    error_backoff_initial_seconds: float = 0.25
    error_backoff_max_seconds: float = 4.0
    consecutive_error_stop_limit: int = 0

    @classmethod
    def load(cls) -> "RunLoopSettings":
        initial = _clamp_float(
            runtime_float("run_loop", "error_backoff_initial_seconds", default=0.25),
            0.25, minimum=0.0, maximum=30.0,
        )
        maximum = _clamp_float(
            runtime_float("run_loop", "error_backoff_max_seconds", default=4.0),
            4.0, minimum=initial, maximum=120.0,
        )
        return cls(
            startup_retry_delay_seconds=_clamp_float(
                runtime_float("run_loop", "startup_retry_delay_seconds", default=0.2),
                0.2, minimum=0.0, maximum=30.0,
            ),
            error_backoff_initial_seconds=initial,
            error_backoff_max_seconds=maximum,
            consecutive_error_stop_limit=_clamp_int(
                runtime_int("run_loop", "consecutive_error_stop_limit", default=0),
                0, minimum=0, maximum=1000,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimeObservabilitySettings:
    slo_window_seconds: float = 3600.0
    slo_max_samples: int = 720
    minimum_ready_ratio: float = 0.95
    maximum_degraded_seconds: float = 300.0
    max_incidents: int = 64

    @classmethod
    def load(cls) -> "RuntimeObservabilitySettings":
        return cls(
            slo_window_seconds=_clamp_float(
                runtime_float("observability", "slo_window_seconds", default=3600.0),
                3600.0, minimum=60.0, maximum=604800.0,
            ),
            slo_max_samples=_clamp_int(
                runtime_int("observability", "slo_max_samples", default=720),
                720, minimum=16, maximum=10000,
            ),
            minimum_ready_ratio=_clamp_float(
                runtime_float("observability", "minimum_ready_ratio", default=0.95),
                0.95, minimum=0.0, maximum=1.0,
            ),
            maximum_degraded_seconds=_clamp_float(
                runtime_float("observability", "maximum_degraded_seconds", default=300.0),
                300.0, minimum=0.0, maximum=604800.0,
            ),
            max_incidents=_clamp_int(
                runtime_int("observability", "max_incidents", default=64),
                64, minimum=4, maximum=4096,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimePolicySettings:
    ai_runtime: AIRuntimeSettings
    public_turn: PublicTurnSettings
    engagement: EngagementSettings
    chess_bridge: ChessBridgeSettings
    health: HealthSettings
    protocol: ProtocolSettings
    run_loop: RunLoopSettings
    observability: RuntimeObservabilitySettings

    @classmethod
    def load(cls) -> "RuntimePolicySettings":
        return cls(
            ai_runtime=AIRuntimeSettings.load(),
            public_turn=PublicTurnSettings.load(),
            engagement=EngagementSettings.load(),
            chess_bridge=ChessBridgeSettings.load(),
            health=HealthSettings.load(),
            protocol=ProtocolSettings.load(),
            run_loop=RunLoopSettings.load(),
            observability=RuntimeObservabilitySettings.load(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ai_runtime": self.ai_runtime.to_dict(),
            "public_turn": self.public_turn.to_dict(),
            "engagement": self.engagement.to_dict(),
            "chess_bridge": self.chess_bridge.to_dict(),
            "health": self.health.to_dict(),
            "protocol": self.protocol.to_dict(),
            "run_loop": self.run_loop.to_dict(),
            "observability": self.observability.to_dict(),
        }


__all__ = [
    "AIRuntimeSettings",
    "ChessBridgeSettings",
    "EngagementSettings",
    "HealthSettings",
    "ProtocolSettings",
    "PublicTurnSettings",
    "RunLoopSettings",
    "RuntimeObservabilitySettings",
    "RuntimePolicySettings",
]
