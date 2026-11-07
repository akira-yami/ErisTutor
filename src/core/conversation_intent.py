from __future__ import annotations

"""Tiện ích chuẩn hóa văn bản cho tầng hội thoại.

Module này **không** còn nhận dạng ý định bằng danh sách câu mẫu. Ý định hội thoại
được main model ghi vào ``semantic_frame.reference`` rồi runtime giải quyết dựa
trên trạng thái phiên và chỉ số lượt. Giữ hàm chuẩn hóa ở đây vì một vài tầng
khác vẫn cần so sánh chuỗi hiển thị, nhưng nó không có quyền quyết định nghĩa.
"""

import re
import unicodedata


def normalize_conversation_text(text: str) -> str:
    """Chuẩn hóa tiếng Việt có/không dấu để so sánh chuỗi, không phân loại ý định."""

    value = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


__all__ = ["normalize_conversation_text"]
