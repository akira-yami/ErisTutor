# -*- coding: utf-8 -*-
"""Chính sách đồng bộ bubble chat với TTS.

Text mặc định chỉ được public khi audio bắt đầu phát. Panel/debug vẫn có thể cập
nhật ngay. TTS lỗi/hủy trước playback không nhả text; không có fallback theo giờ.
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}
_DEFAULT_TEXT_SYNC_TIMEOUT_SECONDS = 30.0


def sync_text_with_tts_enabled() -> bool:
    """Mặc định bật; đặt ERIS_TTS_SYNC_TEXT=0 để quay về hành vi legacy."""

    return os.environ.get("ERIS_TTS_SYNC_TEXT", "1").strip().lower() in _TRUE


def text_sync_timeout_seconds() -> float:
    """Legacy setting reader; public delivery no longer releases text on timeout."""

    raw = os.environ.get("ERIS_TTS_TEXT_SYNC_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_TEXT_SYNC_TIMEOUT_SECONDS
    try:
        return max(0.15, min(120.0, float(raw)))
    except ValueError:
        return _DEFAULT_TEXT_SYNC_TIMEOUT_SECONDS


def tts_event_releases_deferred_text(event_type: str) -> bool:
    """Chỉ playback thật sự mới được nhả text; failure/cancel không phải commit."""

    return str(event_type or "").strip().lower() == "audio_start"


__all__ = [
    "sync_text_with_tts_enabled",
    "text_sync_timeout_seconds",
    "tts_event_releases_deferred_text",
]
