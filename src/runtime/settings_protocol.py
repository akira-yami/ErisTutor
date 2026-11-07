# -*- coding: utf-8 -*-
"""Chuẩn hóa UDP settings thành patch runtime có schema.

Body mới gửi object nested. Payload ``advanced_env`` cũ vẫn được map trong giai
đoạn migration, nhưng không còn được coi là nguồn sự thật.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


from runtime.settings_catalog import RESTART_REQUIRED_PATHS

_RESTART_REQUIRED = set(RESTART_REQUIRED_PATHS)


_LEGACY_ADVANCED_MAP: dict[str, tuple[str, ...]] = {
    "ERIS_REASONING_EFFORT": ("chat", "reasoning_effort"),
    "ERIS_LLM_COMPLETION_CHAT_TOKENS": ("chat", "completion_chat_tokens"),
    "ERIS_LLM_TIMEOUT_CHAT_S": ("chat", "chat_timeout_seconds"),
    "ERIS_LLM_MAX_CONCURRENCY": ("chat", "max_concurrency"),
    "ERIS_NO_FALLBACK_MODE": ("chat", "auto_fallback"),
    "ERIS_SELF_CRITIQUE_ENABLED": ("chat", "critique_mode"),
    "ERIS_CHAT_DIRECTOR_SECOND_PASS": ("chat", "director_second_pass"),
    "ERIS_LLM_PROMPT_TOTAL_TOKENS": ("prompt", "hard_cap_tokens"),
    "ERIS_LLM_PROMPT_HARD_CAP_TOKENS": ("prompt", "hard_cap_tokens"),
    "ERIS_DDS_ENABLED": ("prompt", "dds_enabled"),
    "ERIS_STRICT_GROUNDING": ("prompt", "strict_grounding"),
    "ERIS_MEMORY_SEMANTIC_PARSER": ("memory", "semantic_parser"),
    "ERIS_AI_OBSERVER_WORKERS": ("ai_runtime", "observer_workers"),
    "ERIS_AI_STATE_PERSIST_INTERVAL_SEC": ("ai_runtime", "state_persistence_interval_seconds"),
    "ERIS_PROTOCOL_MAX_MESSAGE_TYPE_CHARS": ("protocol", "max_message_type_chars"),
    "ERIS_PROTOCOL_REJECT_NON_MAPPING": ("protocol", "reject_non_mapping"),
    "ERIS_PROTOCOL_RECORD_UNKNOWN": ("protocol", "record_unknown_messages"),
    "ERIS_RUN_LOOP_STARTUP_RETRY_DELAY_S": ("run_loop", "startup_retry_delay_seconds"),
    "ERIS_RUN_LOOP_ERROR_BACKOFF_INITIAL_S": ("run_loop", "error_backoff_initial_seconds"),
    "ERIS_RUN_LOOP_ERROR_BACKOFF_MAX_S": ("run_loop", "error_backoff_max_seconds"),
    "ERIS_RUN_LOOP_ERROR_STOP_LIMIT": ("run_loop", "consecutive_error_stop_limit"),
    "ERIS_ENGAGEMENT_STOP_ON_GAP": ("engagement", "delivery_stop_on_gap"),
    "ERIS_ENGAGEMENT_LAG_WARNING_EVENTS": ("engagement", "consumer_lag_warning_events"),
    "ERIS_ENGAGEMENT_OPEN_DLQ_WARNING": ("engagement", "open_dead_letter_warning_count"),
    "ERIS_ENGAGEMENT_SNAPSHOT_JOURNAL": ("engagement", "snapshot_journal_enabled"),
    "ERIS_ENGAGEMENT_SNAPSHOT_AUTO_REPAIR": ("engagement", "snapshot_journal_auto_repair"),
    "ERIS_CHESS_BRIDGE_ENABLED": ("chess_bridge", "enabled"),
    "ERIS_CHESS_BRIDGE_HOST": ("chess_bridge", "host"),
    "ERIS_CHESS_BRIDGE_PORT": ("chess_bridge", "port"),
    "ERIS_CHESS_ANALYSIS_DEPTH": ("chess_bridge", "analysis_depth"),
    "ERIS_CHESS_DIRECT_MOVE_RATE": ("chess_bridge", "direct_move_rate"),
    "ERIS_STOCKFISH_ENABLED": ("chess_bridge", "stockfish_enabled"),
    "ERIS_STOCKFISH_PATH": ("chess_bridge", "stockfish_path"),
    "ERIS_STOCKFISH_THREADS": ("chess_bridge", "stockfish_threads"),
    "ERIS_STOCKFISH_HASH_MB": ("chess_bridge", "stockfish_hash_mb"),
    "ERIS_STOCKFISH_MULTIPV": ("chess_bridge", "stockfish_multipv"),
    "ERIS_STOCKFISH_MOVETIME_MS": ("chess_bridge", "stockfish_movetime_ms"),
    "ERIS_STOCKFISH_ON_DEMAND_MOVETIME_MS": ("chess_bridge", "stockfish_on_demand_movetime_ms"),
    "ERIS_STOCKFISH_STARTUP_TIMEOUT_SECONDS": ("chess_bridge", "stockfish_startup_timeout_seconds"),
    "ERIS_STOCKFISH_ANALYSIS_TIMEOUT_SECONDS": ("chess_bridge", "stockfish_analysis_timeout_seconds"),
    "ERIS_CHESS_MAX_BODY_BYTES": ("chess_bridge", "max_body_bytes"),
    "ERIS_MEMORY_DIGEST_MAINTENANCE": ("memory", "maintenance_enabled"),
    "ERIS_MEMORY_MAINTENANCE_BUDGET": ("memory", "maintenance_budget"),
    "ERIS_PERFORMANCE_ACTS_ENABLED": ("stage", "performance_acts_enabled"),
    "ERIS_INTERRUPT_ENABLED": ("stage", "interrupt_enabled"),
    "ERIS_STAGE_PROTOCOL": ("stage", "protocol_enabled"),
    "ERIS_STAGE_PROFILE": ("stage", "performance_profile"),
    "ERIS_STAGE_SEGMENT_TTS": ("stage", "segment_tts"),
    "ERIS_TTS_SEGMENT_DISPATCH": ("stage", "segment_dispatch"),
    "ERIS_TTS_TASK_MANAGER": ("stage", "tts_task_manager"),
    "ERIS_TTS_PREFETCH_WORKERS": ("stage", "prefetch_workers"),
    "ERIS_STAGE_MAX_TTS_SEGMENTS": ("stage", "max_tts_segments"),
    "ERIS_TTS_STAGE_LOCALIZE": ("tts", "stage_localize"),
    "ERIS_TTS_AUDIO_POLISH": ("tts", "audio_polish"),
    "ERIS_TUTOR_ENABLED": ("tutor", "enabled"),
    "ERIS_TUTOR_QUICK_SEARCH_ENABLED": ("tutor", "quick_search_enabled"),
    "ERIS_TUTOR_RESEARCH_ENABLED": ("tutor", "research_enabled"),
    "ERIS_TUTOR_SELF_SEARCH_ENABLED": ("tutor", "self_search_enabled"),
    "ERIS_TUTOR_STALE_GUARD": ("tutor", "stale_result_guard_enabled"),
    "ERIS_TUTOR_RESEARCH_PERSIST": ("tutor", "persist_research_memory"),
}


@dataclass(frozen=True, slots=True)
class NormalizedSettingsMessage:
    scope: str
    patch: dict[str, Any]
    restart_required_paths: frozenset[str]
    rejected_fields: tuple[str, ...] = ()
    legacy_payload: bool = False


def normalize_settings_message(msg: Mapping[str, Any]) -> NormalizedSettingsMessage:
    scope = str(msg.get("scope", "runtime") or "runtime").strip().lower()
    raw = msg.get("settings")

    # Contract mới: mọi scope đều gửi object nested. TTS vẫn nhận payload phẳng
    # cũ để các body trước schema v14 không chết ngay trong ngày nâng cấp.
    if isinstance(raw, Mapping):
        from runtime.runtime_settings_store import RuntimeSettingsStore

        all_roots = {str(key) for key in RuntimeSettingsStore.defaults().keys() if str(key) != "version"}
        allowed_roots = {
            "advanced": all_roots - {"tts"},
            "voice": {"tts"},
            "tts": {"tts"},
            "danger": {"danger_zone"},
            "personality": {"personality"},
            "runtime": all_roots,
        }.get(scope, set())
        patch = {str(k): v for k, v in raw.items() if str(k) in allowed_roots}
        rejected = tuple(str(k) for k in raw.keys() if str(k) not in allowed_roots)
        return NormalizedSettingsMessage(
            scope="tts" if scope == "voice" else scope,
            patch=patch,
            restart_required_paths=frozenset(_RESTART_REQUIRED),
            rejected_fields=rejected,
        )

    if scope in {"tts", "voice"}:
        language = _language(msg.get("tts_language", "vi"))
        patch = {
            "tts": {
                "enabled": _bool(msg.get("tts_enabled", True)),
                "language": language,
                "voice": str(msg.get("tts_voice", "Aoede") or "Aoede").strip(),
                "quality": str(msg.get("tts_quality", "high") or "high").strip().lower(),
                "volume": _float(msg.get("tts_volume", 1.0), 1.0),
                "speed": _float(msg.get("tts_speed", 1.0), 1.0),
            }
        }
        return NormalizedSettingsMessage(scope="tts", patch=patch, restart_required_paths=frozenset())

    legacy = msg.get("advanced_env")
    if scope == "advanced" and isinstance(legacy, Mapping):
        patch: dict[str, Any] = {}
        rejected: list[str] = []
        for key, value in legacy.items():
            path = _LEGACY_ADVANCED_MAP.get(str(key))
            if path is None:
                rejected.append(str(key))
                continue
            normalized = _normalize_legacy_value(str(key), value)
            _set_path(patch, path, normalized)
        return NormalizedSettingsMessage(
            scope="advanced",
            patch=patch,
            restart_required_paths=frozenset(_RESTART_REQUIRED),
            rejected_fields=tuple(rejected),
            legacy_payload=True,
        )

    return NormalizedSettingsMessage(
        scope=scope,
        patch={},
        restart_required_paths=frozenset(_RESTART_REQUIRED),
        rejected_fields=("missing_settings_payload",),
    )


def _normalize_legacy_value(key: str, value: Any) -> Any:
    text = str(value or "").strip()
    if key == "ERIS_NO_FALLBACK_MODE":
        return not _bool(text)
    if key == "ERIS_SELF_CRITIQUE_ENABLED":
        return "advisory" if _bool(text) else "off"
    if key == "ERIS_CHAT_DIRECTOR_SECOND_PASS":
        return "off"
    if key in {
        "ERIS_LLM_COMPLETION_CHAT_TOKENS", "ERIS_LLM_TIMEOUT_CHAT_S", "ERIS_LLM_MAX_CONCURRENCY",
        "ERIS_LLM_PROMPT_TOTAL_TOKENS", "ERIS_LLM_PROMPT_HARD_CAP_TOKENS", "ERIS_TTS_PREFETCH_WORKERS",
        "ERIS_AI_OBSERVER_WORKERS",
        "ERIS_PROTOCOL_MAX_MESSAGE_TYPE_CHARS", "ERIS_RUN_LOOP_ERROR_STOP_LIMIT",
        "ERIS_ENGAGEMENT_LAG_WARNING_EVENTS", "ERIS_ENGAGEMENT_OPEN_DLQ_WARNING",
        "ERIS_STAGE_MAX_TTS_SEGMENTS",
    }:
        try:
            return int(float(text))
        except (TypeError, ValueError):
            return text
    if key in {
        "ERIS_AI_STATE_PERSIST_INTERVAL_SEC",
        "ERIS_RUN_LOOP_STARTUP_RETRY_DELAY_S",
        "ERIS_RUN_LOOP_ERROR_BACKOFF_INITIAL_S",
        "ERIS_RUN_LOOP_ERROR_BACKOFF_MAX_S",
    }:
        try:
            return float(text)
        except (TypeError, ValueError):
            return text
    if key in {
        "ERIS_DDS_ENABLED", "ERIS_STRICT_GROUNDING", "ERIS_MEMORY_SEMANTIC_PARSER",
        "ERIS_MEMORY_DIGEST_MAINTENANCE", "ERIS_PERFORMANCE_ACTS_ENABLED",
        "ERIS_INTERRUPT_ENABLED", "ERIS_STAGE_PROTOCOL", "ERIS_STAGE_SEGMENT_TTS",
        "ERIS_TTS_TASK_MANAGER", "ERIS_TTS_STAGE_LOCALIZE", "ERIS_TTS_AUDIO_POLISH",
        "ERIS_TUTOR_ENABLED", "ERIS_TUTOR_QUICK_SEARCH_ENABLED", "ERIS_TUTOR_RESEARCH_ENABLED",
        "ERIS_TUTOR_SELF_SEARCH_ENABLED", "ERIS_TUTOR_STALE_GUARD", "ERIS_TUTOR_RESEARCH_PERSIST",
        "ERIS_PROTOCOL_REJECT_NON_MAPPING", "ERIS_PROTOCOL_RECORD_UNKNOWN",
        "ERIS_ENGAGEMENT_STOP_ON_GAP",
    }:
        return _bool(text)
    return text


def _set_path(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    node = payload
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "0", "false", "off", "no"}


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _language(value: Any) -> str:
    raw = str(value or "vi").strip().lower()
    aliases = {
        "auto": "vi", "jp": "ja", "japanese": "ja", "ja-jp": "ja",
        "vi-vn": "vi", "vietnamese": "vi",
        "en-us": "en", "english": "en",
    }
    return aliases.get(raw, raw)


__all__ = ["NormalizedSettingsMessage", "normalize_settings_message"]
