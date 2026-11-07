# -*- coding: utf-8 -*-
"""Schema hiển thị cho Settings panel.

RuntimeSettingsStore vẫn là nguồn sự thật về giá trị. Module này chỉ mô tả UI:
field nào thuộc owner nào, nằm ở tab nào, kiểu input gì và có cần restart không.
Không có API key/secret trong catalog này.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from runtime.runtime_settings_store import RuntimeSettingsStore


RESTART_REQUIRED_PATHS: frozenset[str] = frozenset({
    # Bootstrap / topology
    "architecture.mode",
    "chat.max_concurrency",
    "social_observer.primary_model",
    "social_observer.deep_model",
    "social_observer.fallback_model",
    "social_observer.workers",
    "ai_runtime.observer_workers",
    "ai_runtime.state_persistence_interval_seconds",
    "prompt.strict_grounding",
    "memory.semantic_parser",
    # Typed runtime objects currently reconstructed as a unit.
    "public_turn.reply_window_seconds",
    "public_turn.collision_grace_seconds",
    "engagement.enabled",
    "engagement.max_events",
    "engagement.persistence_interval_seconds",
    "engagement.signal_importance_threshold",
    "engagement.activity_key_cache_size",
    "engagement.activity_key_retention",
    "engagement.max_terminal_engagements",
    "engagement.terminal_retention_seconds",
    "engagement.event_consumer_batch_size",
    "engagement.delivery_lease_seconds",
    "engagement.delivery_max_attempts",
    "engagement.delivery_backoff_initial_seconds",
    "engagement.delivery_backoff_max_seconds",
    "engagement.max_dead_letters",
    "engagement.delivery_stop_on_gap",
    "engagement.consumer_lag_warning_events",
    "engagement.open_dead_letter_warning_count",
    "engagement.max_delivery_receipts",
    "engagement.delivery_split_poison_batches",
    "engagement.delivery_split_after_attempts",
    "engagement.managed_consumer_poll_budget",
    "engagement.snapshot_journal_enabled",
    "engagement.snapshot_journal_auto_repair",
    "chess_bridge.enabled",
    "chess_bridge.host",
    "chess_bridge.port",
    "chess_bridge.analysis_depth",
    "chess_bridge.direct_move_rate",
    "chess_bridge.stockfish_enabled",
    "chess_bridge.stockfish_path",
    "chess_bridge.stockfish_threads",
    "chess_bridge.stockfish_hash_mb",
    "chess_bridge.stockfish_multipv",
    "chess_bridge.stockfish_movetime_ms",
    "chess_bridge.stockfish_on_demand_movetime_ms",
    "chess_bridge.stockfish_startup_timeout_seconds",
    "chess_bridge.stockfish_analysis_timeout_seconds",
    "chess_bridge.max_body_bytes",
    "observability.slo_window_seconds",
    "observability.slo_max_samples",
    "observability.minimum_ready_ratio",
    "observability.maximum_degraded_seconds",
    "observability.max_incidents",
    "health.max_events",
    "hardening.profile",
    "hardening.production_validation_enabled",
    "hardening.production_validation_turns",
    "hardening.production_validation_dir",
    "hardening.panel_ack_required",
    "hardening.provider_registry_strict",
    "hardening.release_manifest_path",
    "hardening.release_guard_enabled",
    "hardening.release_guard_fail_closed",
    "protocol.max_message_type_chars",
    "protocol.reject_non_mapping",
    "protocol.record_unknown_messages",
    "run_loop.startup_retry_delay_seconds",
    "run_loop.error_backoff_initial_seconds",
    "run_loop.error_backoff_max_seconds",
    "run_loop.consecutive_error_stop_limit",
})


SECTION_META: dict[str, dict[str, str]] = {
    "architecture": {"label": "Kiến trúc khởi động", "group": "cognition", "surface": "advanced", "owner": "brain_runtime"},
    "chat": {"label": "Hội thoại & định tuyến model", "group": "cognition", "surface": "advanced", "owner": "brain_runtime"},
    "pedagogy": {"label": "Pedagogical controller", "group": "cognition", "surface": "advanced", "owner": "brain_runtime"},
    "personality": {"label": "Tính cách & ranh giới", "group": "cognition", "surface": "advanced", "owner": "brain_runtime"},
    "prompt": {"label": "Prompt & grounding", "group": "cognition", "surface": "advanced", "owner": "brain_runtime"},
    "social_observer": {"label": "Não phụ / Social observer", "group": "cognition", "surface": "advanced", "owner": "brain_runtime"},
    "commentary": {"label": "Bình luận chủ động", "group": "presence", "surface": "advanced", "owner": "brain_runtime"},
    "initiative": {"label": "Chủ động mở lời", "group": "presence", "surface": "advanced", "owner": "brain_runtime"},
    "discovery": {"label": "Khám phá & tín hiệu địa phương", "group": "presence", "surface": "advanced", "owner": "brain_runtime"},
    "memory": {"label": "Memory runtime", "group": "memory", "surface": "advanced", "owner": "brain_runtime"},
    "tutor": {"label": "Tutor / nghiên cứu", "group": "memory", "surface": "advanced", "owner": "brain_runtime"},
    "stage": {"label": "Stage & chia đoạn", "group": "stage", "surface": "advanced", "owner": "brain_runtime"},
    "tts": {"label": "TTS & diễn giọng", "group": "stage", "surface": "voice", "owner": "brain_runtime"},
    "capabilities": {"label": "Khả năng hệ thống", "group": "tools", "surface": "advanced", "owner": "brain_runtime"},
    "project_ops": {"label": "Project operations", "group": "tools", "surface": "advanced", "owner": "brain_runtime"},
    "chess_bridge": {"label": "Chess bridge", "group": "tools", "surface": "advanced", "owner": "brain_runtime"},
    "ai_runtime": {"label": "AI runtime", "group": "runtime", "surface": "advanced", "owner": "brain_runtime"},
    "public_turn": {"label": "Public turn timing", "group": "runtime", "surface": "advanced", "owner": "brain_runtime"},
    "engagement": {"label": "Engagement queue", "group": "runtime", "surface": "advanced", "owner": "brain_runtime"},
    "observability": {"label": "Observability / SLO", "group": "runtime", "surface": "advanced", "owner": "brain_runtime"},
    "health": {"label": "Health history", "group": "runtime", "surface": "advanced", "owner": "brain_runtime"},
    "protocol": {"label": "UDP protocol guard", "group": "runtime", "surface": "advanced", "owner": "brain_runtime"},
    "run_loop": {"label": "Run-loop recovery", "group": "runtime", "surface": "advanced", "owner": "brain_runtime"},
    "hardening": {"label": "Hardening & release guard", "group": "safety", "surface": "advanced", "owner": "brain_runtime"},
    "danger_zone": {"label": "Chính sách Danger Zone", "group": "safety", "surface": "advanced", "owner": "brain_runtime"},
}

GROUP_META: tuple[dict[str, str], ...] = (
    {"id": "cognition", "label": "Nhận thức"},
    {"id": "presence", "label": "Hiện diện"},
    {"id": "memory", "label": "Memory & Tutor"},
    {"id": "stage", "label": "Stage & Voice"},
    {"id": "tools", "label": "Công cụ & Project"},
    {"id": "runtime", "label": "Runtime hệ thống"},
    {"id": "safety", "label": "An toàn & hardening"},
)

ENUMS: dict[str, tuple[str, ...]] = {
    'architecture.mode': ('ai_orchestrated',),
    'capabilities.source': ('registry',),
    'chat.critique_mode': ('advisory', 'bounded_refine', 'off'),
    'chat.director_candidate_mode': ('adaptive', 'always', 'off'),
    'chat.director_second_pass': ('adaptive', 'always', 'off'),
    'chat.language': ('en', 'ja', 'vi'),
    'chat.reasoning_effort': ('high', 'low', 'medium'),
    'chat.semantic_authority_mode': ('primary_model',),
    'hardening.profile': ('canary', 'development', 'stable'),
    'memory.maintenance_budget': ('large', 'medium', 'small'),
    'personality.provocation_policy': ('boundary_only', 'character_safe', 'restrained'),
    'personality.retort_mode': ('balanced', 'off', 'restrained', 'sharp'),
    'personality.style_repair_mode': ('direct', 'self_aware_dry', 'soft'),
    'project_ops.autonomy_mode': ('full', 'guarded', 'off', 'read_only'),
    'stage.performance_profile': ('conversation', 'custom'),
    'stage.segment_dispatch': ('inline', 'queued'),
    'tts.language': ('en', 'ja', 'vi'),
    'tts.provider': ('elevenlabs', 'gemini'),
    'tts.quality': ('balanced', 'high'),
}

RANGES: dict[str, tuple[float, float, float]] = {
    'ai_runtime.observer_workers': (1, 8, 1),
    'ai_runtime.state_persistence_interval_seconds': (0, 60, 0.1),
    'chat.chat_timeout_seconds': (5, 120, 1),
    'chat.companion_max_reasoning_threshold': (0.5, 1.0, 0.01),
    'chat.companion_pro_threshold': (0.2, 0.95, 0.01),
    'chat.completion_chat_tokens': (384, 4096, 1),
    'chat.max_concurrency': (1, 16, 1),
    'chat.max_constrained_reruns': (0, 2, 1),
    'chat.support_confidence_threshold': (0.05, 0.95, 0.01),
    'pedagogy.minimum_dwell_seconds': (0.5, 60.0, 0.5),
    'pedagogy.maximum_dwell_seconds': (1.0, 300.0, 1.0),
    'pedagogy.max_autonomous_chunks': (0, 8, 1),
    'pedagogy.max_repair_attempts': (0, 5, 1),
    'pedagogy.max_consecutive_stalls': (1, 8, 1),
    'pedagogy.episode_expiry_seconds': (60.0, 86400.0, 60.0),
    'chess_bridge.analysis_depth': (1, 4, 1),
    'chess_bridge.direct_move_rate': (0.0, 1.0, 0.01),
    'chess_bridge.stockfish_threads': (1, 32, 1),
    'chess_bridge.stockfish_hash_mb': (16, 4096, 16),
    'chess_bridge.stockfish_multipv': (1, 8, 1),
    'chess_bridge.stockfish_movetime_ms': (25, 10000, 25),
    'chess_bridge.stockfish_on_demand_movetime_ms': (50, 30000, 50),
    'chess_bridge.stockfish_startup_timeout_seconds': (0.5, 30.0, 0.5),
    'chess_bridge.stockfish_analysis_timeout_seconds': (0.5, 60.0, 0.5),
    'chess_bridge.max_body_bytes': (4096, 1048576, 1),
    'chess_bridge.port': (1024, 65535, 1),
    'commentary.desired_interval_seconds': (20, 1800, 1),
    'commentary.maximum_quiet_seconds': (30, 3600, 1),
    'commentary.startup_delay_seconds': (0, 120, 1),
    'engagement.activity_key_cache_size': (32, 4096, 1),
    'engagement.activity_key_retention': (16, 4096, 1),
    'engagement.consumer_lag_warning_events': (1, 1000000, 1),
    'engagement.delivery_backoff_initial_seconds': (0, 3600, 1),
    'engagement.delivery_backoff_max_seconds': (0, 86400, 1),
    'engagement.delivery_lease_seconds': (1, 3600, 1),
    'engagement.delivery_max_attempts': (1, 100, 1),
    'engagement.event_consumer_batch_size': (1, 1024, 1),
    'engagement.max_dead_letters': (1, 4096, 1),
    'engagement.max_events': (24, 4096, 1),
    'engagement.max_terminal_engagements': (0, 4096, 1),
    'engagement.open_dead_letter_warning_count': (1, 4096, 1),
    'engagement.persistence_interval_seconds': (0, 60, 0.1),
    'engagement.signal_importance_threshold': (0.0, 1.0, 0.01),
    'engagement.terminal_retention_seconds': (0, 31536000, 1),
    'hardening.production_validation_turns': (20, 30, 1),
    'health.max_events': (16, 4096, 1),
    'initiative.global_hourly_budget': (0, 120, 1),
    'initiative.minimum_interval_seconds': (15, 3600, 1),
    'observability.max_incidents': (4, 4096, 1),
    'observability.maximum_degraded_seconds': (0, 604800, 1),
    'observability.minimum_ready_ratio': (0.0, 1.0, 0.01),
    'observability.slo_max_samples': (16, 10000, 1),
    'observability.slo_window_seconds': (60, 604800, 1),
    'project_ops.max_repair_attempts': (0, 5, 1),
    'prompt.hard_cap_tokens': (0, 1000000, 1),
    'public_turn.collision_grace_seconds': (0, 5, 0.1),
    'public_turn.reply_window_seconds': (5, 300, 1),
    'stage.max_tts_segments': (1, 12, 1),
    'stage.min_split_chars': (24, 160, 1),
    'stage.prefetch_workers': (1, 8, 1),
    'tts.signature_ending_rate': (0.0, 0.8, 0.01),
    'tts.speech_persona_cuteness': (0.0, 0.35, 0.01),
    'tts.speech_persona_warmth': (0.0, 0.5, 0.01),
    'tts.speed': (0.5, 2.0, 0.01),
    'tts.volume': (0.0, 2.0, 0.01),
}

LABELS: dict[str, str] = {
    "enabled": "Bật",
    "provider": "Provider",
    "language": "Ngôn ngữ",
    "voice": "Giọng Gemini",
    "quality": "Chất lượng",
    "volume": "Âm lượng",
    "speed": "Tốc độ",
    "elevenlabs_voice_id": "ElevenLabs Voice ID",
    "elevenlabs_voice_id_vi": "Giọng ElevenLabs tiếng Việt",
    "elevenlabs_voice_id_ja": "Giọng ElevenLabs tiếng Nhật (trống: dùng giọng cũ)",
    "elevenlabs_model": "ElevenLabs model",
    "voice_director_enabled": "Voice director từ xa (legacy)",
    "reasoning_effort": "Mức suy luận",
    "completion_chat_tokens": "Token trả lời tối đa",
    "chat_timeout_seconds": "Timeout hội thoại (giây)",
    "max_concurrency": "Số request đồng thời",
    "auto_fallback": "Tự dùng model dự phòng",
    "companion_routing_enabled": "Định tuyến Flash/Pro",
    "companion_pro_threshold": "Ngưỡng chuyển sang Pro",
    "companion_max_reasoning_threshold": "Ngưỡng suy luận tối đa",
    "background_digest": "Digest memory nền",
    "maintenance_enabled": "Bảo trì memory khi rảnh",
    "maintenance_budget": "Ngân sách bảo trì",
    "self_search_enabled": "Tutor tự tìm kiếm",
    "quick_search_enabled": "Tra cứu nhanh",
    "research_enabled": "Nghiên cứu sâu",
    "persist_research_memory": "Lưu nghiên cứu vào memory",
    "performance_acts_enabled": "Performance acts",
    "interrupt_enabled": "Cho phép ngắt lời",
    "protocol_enabled": "Stage protocol",
    "segment_tts": "TTS theo segment",
    "tts_task_manager": "TTS task manager",
    "stage_localize": "Dịch TTS hậu kỳ (legacy)",
    "speech_persona_polish": "Làm mềm lời nói",
    "speech_persona_warmth": "Độ ấm giọng",
    "speech_persona_cuteness": "Độ đáng yêu",
    "signature_ending_enabled": "Bật đuôi câu đặc trưng",
    "signature_ending_rate": "Tần suất đuôi câu",
    "audio_polish": "Audio polish",
    "autonomy_mode": "Mức tự chủ project",
    "ask_when_uncertain": "Hỏi khi thiếu dữ kiện quan trọng",
    "proceed_without_user_reply": "Tiếp tục khi chỉ thiếu chi tiết nhỏ",
    "auto_rollback_on_failure": "Tự rollback khi lỗi",
    "auto_package_patch": "Tự đóng gói patch",
    "max_repair_attempts": "Số vòng tự sửa",
    "local_signal_enabled": "Tín hiệu địa phương",
    "user_location": "Vị trí người dùng",
    "local_signal_scope": "Phạm vi tín hiệu địa phương",
    "allow_romantic_flirt": "Cho phep flirt romantic",
    "allow_sexual_content": "Cho phep noi dung 18+",
    "romantic_flirt_opt_in": "Nguoi dung opt-in flirt",
    "require_adult_flirt_opt_in": "Yeu cau adult flirt opt-in",
    "age_band": "Nhom tuoi nguoi dung",
    "allow_screen_hook": "Cho phep hook man hinh",
    "allow_continuity_hook": "Cho phep hook continuity",
    "allow_anime_moves": "Cho phep anime moves",
    "autonomous_expression_enabled": "Cho phép public expression (explicit boundary)",
    "surface_only": "Chỉ dùng DeepSeek Surface để viết lời",
    "open_topic": "Cho phép Surface tự chọn chủ đề",
    "preference_updated_at": "Thời điểm cập nhật preference",
    "desired_interval_seconds": "Khoảng bình luận mong muốn (giây)",
    "maximum_quiet_seconds": "Im lặng tối đa (giây)",
    "global_hourly_budget": "Ngân sách chủ động mỗi giờ",
    "minimum_interval_seconds": "Khoảng cách tối thiểu (giây)",
    "strict_grounding": "Grounding nghiêm ngặt",
    "dds_enabled": "DDS",
    "hard_cap_tokens": "Giới hạn prompt token",
    "profile": "Profile",
}


def _kind(value: Any, path: str) -> str:
    if path in ENUMS:
        return "enum"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def _humanize(key: str) -> str:
    if key in LABELS:
        return LABELS[key]
    words = key.replace("_seconds", " (giây)").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else key


def build_settings_catalog(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    defaults = RuntimeSettingsStore.defaults()
    current = settings if isinstance(settings, Mapping) else defaults
    fields: list[dict[str, Any]] = []
    sections: dict[str, dict[str, Any]] = {}

    for section, default_values in defaults.items():
        if section == "version" or not isinstance(default_values, Mapping):
            continue
        meta = dict(SECTION_META.get(section, {
            "label": _humanize(section),
            "group": "runtime",
            "surface": "advanced",
            "owner": "brain_runtime",
        }))
        sections[section] = meta
        current_values = current.get(section, {}) if isinstance(current, Mapping) else {}
        for key, default_value in default_values.items():
            path = f"{section}.{key}"
            value = current_values.get(key, default_value) if isinstance(current_values, Mapping) else default_value
            field: dict[str, Any] = {
                "path": path,
                "section": section,
                "key": key,
                "label": _humanize(key),
                "kind": _kind(default_value, path),
                "default": default_value,
                "value": value,
                "owner": meta["owner"],
                "surface": meta["surface"],
                "restart_required": path in RESTART_REQUIRED_PATHS,
            }
            if path in ENUMS:
                field["options"] = list(ENUMS[path])
            if path in RANGES:
                lo, hi, step = RANGES[path]
                field.update({"min": lo, "max": hi, "step": step})
            fields.append(field)

    return {
        "schema_version": int(defaults.get("version", 0) or 0),
        "groups": [dict(item) for item in GROUP_META],
        "sections": sections,
        "fields": fields,
        "responsibilities": {
            "body_local": "user://eris_settings.json: cửa sổ, overlay, autostart và bản nháp UI.",
            "brain_runtime": "runtime_settings.json do Brain sở hữu; panel chỉ đọc/ghi qua settings_health/settings_update.",
            "bootstrap_credentials": "config/secrets.json và provider profile; chỉ tab Kết nối & dữ liệu được ghi, cần restart Brain.",
            "maintenance": "Danger Zone gọi maintenance protocol; không giả làm setting runtime.",
        },
    }


__all__ = ["RESTART_REQUIRED_PATHS", "build_settings_catalog"]
