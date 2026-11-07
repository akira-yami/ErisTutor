# -*- coding: utf-8 -*-
"""Feature flag cho bốn ranh giới kiến trúc public-speech.

Mặc định mới là fail-closed. Code cũ vẫn còn để rollback, nhưng chỉ chạy khi
flag tương ứng được bật rõ ràng. Cách này bớt hào hứng hơn việc xóa sạch rồi cầu
nguyện lúc deploy, nhưng đáng tin hơn đáng kể.
"""
from __future__ import annotations

import os
from typing import Any


def _flag(setting_name: str, env_name: str, default: bool = False) -> bool:
    try:
        from runtime.runtime_settings_store import runtime_bool
        return bool(runtime_bool("architecture", setting_name, default=default))
    except Exception:
        raw: Any = os.getenv(env_name)
        if raw is None:
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "bật", "bat"}


def private_cognition_surface_export_enabled() -> bool:
    """Cho PrivateMind/PrivateLife xuất candidate speech kiểu cũ (mặc định tắt)."""
    return _flag("private_cognition_surface_export", "ERIS_PRIVATE_COGNITION_SURFACE_EXPORT", False)


def surface_cross_worker_fallback_enabled() -> bool:
    """Cho worker khác giả làm Surface khi Surface chết (mặc định tắt)."""
    return _flag("surface_cross_worker_fallback", "ERIS_SURFACE_REALIZER_COMPANION_FALLBACK", False)


def legacy_model_lanes_enabled() -> bool:
    """Khởi động local/open-model/style lane cũ (mặc định tắt)."""
    return _flag("legacy_model_lanes_enabled", "ERIS_LEGACY_MODEL_LANES_ENABLED", False)


def inner_diary_model_enabled() -> bool:
    """Umbrella legacy: cho Inner Diary dùng model riêng (V80 mặc định bật)."""
    return _flag("inner_diary_model_enabled", "ERIS_INNER_DIARY_MODEL_ENABLED", True)


def inner_diary_deep_reflection_enabled() -> bool:
    """Cho DeepSeek chạy reflection episode thưa, không điền 5 ô live."""
    return _flag("inner_diary_deep_reflection_enabled", "ERIS_INNER_DIARY_DEEP_REFLECTION_ENABLED", True)


def private_residue_curator_enabled() -> bool:
    """Bật Gemma Residue Curator; thiếu credential vẫn giữ deterministic retention."""
    return False


def background_tutor_enabled() -> bool:
    """Retired: tutor/research background runtime is no longer started."""
    return False


def event_trace_enabled() -> bool:
    """Ghi trace xuyên candidate -> governor -> Surface -> TTS -> outcome."""
    return _flag("event_trace_enabled", "ERIS_PUBLIC_EVENT_TRACE_ENABLED", True)


PUBLIC_INTENT_OWNERS = frozenset({"intent_planner", "social_initiative"})


def is_public_intent_owner(owner: object) -> bool:
    return str(owner or "").strip().lower() in PUBLIC_INTENT_OWNERS


__all__ = [
    "PUBLIC_INTENT_OWNERS", "is_public_intent_owner",
    "private_cognition_surface_export_enabled", "surface_cross_worker_fallback_enabled",
    "legacy_model_lanes_enabled", "inner_diary_model_enabled",
    "inner_diary_deep_reflection_enabled", "private_residue_curator_enabled", "background_tutor_enabled",
    "event_trace_enabled",
]
