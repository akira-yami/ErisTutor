from __future__ import annotations

import re
import unicodedata


_SPACE_RE = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt để match mềm, không phá text gốc dùng để trả lời."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D")


def normalize_vi(text: str) -> str:
    """Chuẩn hóa nhẹ cho phân loại intent/fact."""
    lowered = strip_accents(text).lower()
    lowered = lowered.replace("’", "'").replace("“", '"').replace("”", '"')
    lowered = _SPACE_RE.sub(" ", lowered).strip()
    return lowered


def compact_spaces(text: str) -> str:
    """Giữ dấu nhưng dọn khoảng trắng."""
    return _SPACE_RE.sub(" ", text).strip()


def looks_like_question(text: str) -> bool:
    """
    Nhận diện câu hỏi theo nghĩa hội thoại Việt.

    Không cần hoàn hảo. Phần này chỉ phục vụ ledger đếm deterministic,
    không nên biến thành bộ suy diễn tâm lý cấp thần rởm.
    """
    n = normalize_vi(text)
    if "?" in text:
        return True

    question_markers = (
        "khong",
        "khong?",
        "nhir",
        "nhi",
        "ha",
        "a",
        "sao",
        "gi",
        "nao",
        "bao nhieu",
        "co ",
        "duoc chu",
        "duoc khong",
        "la gi",
        "the nao",
        "bao lau",
    )
    return any(marker in n for marker in question_markers)


def visible_len(text: str) -> int:
    """Độ dài phục vụ so sánh câu dài nhất, sau khi nén khoảng trắng."""
    return len(compact_spaces(text))
