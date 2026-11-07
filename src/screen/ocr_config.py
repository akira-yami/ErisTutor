from __future__ import annotations

"""Công tắc duy nhất cho OCR màn hình.

OCR mặc định TẮT: đã có vision đọc màn hình, nên không nạp RapidOCR/Tesseract,
không pip tự cài và không ép OCR từ chat. UIA text vẫn chạy bình thường.
Đặt ``ERIS_SCREEN_OCR_ENABLED=1`` để bật lại hành vi cũ.
"""

import os

_TRUTHY = {"1", "true", "yes", "on"}


def screen_ocr_enabled() -> bool:
    """Đọc env tại thời điểm gọi để test và người dùng đổi được không cần import lại."""

    return os.getenv("ERIS_SCREEN_OCR_ENABLED", "0").strip().lower() in _TRUTHY


__all__ = ["screen_ocr_enabled"]
