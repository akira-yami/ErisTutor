# -*- coding: utf-8 -*-
"""Tên model và token tương thích nằm ở đúng một boundary.

Code nội bộ dùng tên vai trò ``open model``. Chuỗi provider cũ chỉ tồn tại tại
đây để đọc cấu hình đã phát hành và gọi đúng model bên ngoài.
"""
from __future__ import annotations

DEFAULT_OPEN_MODEL_ID = "gemma-4-31b-it"
LEGACY_OPEN_MODEL_PREFIX = "gemma"
LEGACY_OPEN_MODEL_MODE = "gemma"


def requires_open_model_hygiene(model_name: object) -> bool:
    """Model tương thích này cần payload Gemini tối giản hơn model native."""

    return str(model_name or "").strip().lower().startswith(LEGACY_OPEN_MODEL_PREFIX)


__all__ = [
    "DEFAULT_OPEN_MODEL_ID",
    "LEGACY_OPEN_MODEL_MODE",
    "LEGACY_OPEN_MODEL_PREFIX",
    "requires_open_model_hygiene",
]
