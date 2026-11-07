# -*- coding: utf-8 -*-
"""tts_markup.py — render ProsodyPlan thành transcript cho Gemini TTS.

Gemini TTS nhận chỉ dẫn tự nhiên và audio tags bằng tiếng Anh.  Ta chỉ render
tag ở tầng TTS, không đưa tag vào UI/memory.  Các pause ở đây cố ý nhẹ: dấu
"…" dễ được TTS đọc như khoảng dừng hơn là chữ "short pause".
"""

from __future__ import annotations

import re

from runtime.prosody_director import ProsodyPlan


_LEADING_TAG_RE = re.compile(r"^\s*\[[^\]]+\]\s*")


def render_tts_markup(text: str, plan: ProsodyPlan) -> str:
    lines = _normalize_lines(text)
    if not lines:
        return ""

    # Giữ line break như "beat" diễn xuất. Trước đây ta flatten thành một dòng,
    # khiến Gemini mất dấu chỗ nào cần nghỉ ngắn/chỗ nào là nhịp châm chọc.
    marked_lines = [_apply_pause(line, plan) for line in lines]
    marked = "\n".join(marked_lines).strip()
    if plan.opening_tag and not _LEADING_TAG_RE.match(marked):
        marked = f"{plan.opening_tag} {marked}".strip()
    return marked


def _normalize_lines(text: str) -> list[str]:
    """Chuẩn hoá transcript nhưng không xoá nhịp dòng.

    TTS Gemini hiểu prompt tự nhiên tốt hơn khi transcript có line break rõ.
    Mỗi dòng nên là một breath group ngắn, không phải đoạn văn dài.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"(?:```[\w.+-]*\n?[\s\S]*?(?:```|\Z)|~~~[\w.+-]*\n?[\s\S]*?(?:~~~|\Z))", "", raw, flags=re.S)
    raw = re.sub(r"`[^`\n]*`", "", raw)
    out: list[str] = []
    for line in raw.split("\n"):
        clean = " ".join(line.split()).strip()
        if clean:
            out.append(clean)
    return out


def _apply_pause(text: str, plan: ProsodyPlan) -> str:
    if plan.pause_level == "none":
        return text
    if len(text) < 12:
        return text

    # Không chèn dấu ba chấm để ép nhịp. Gemini thường diễn ``…`` như một cú
    # dừng cứng, nhất là sau từ mở đầu ngắn ("Ừ,…"), khiến một hơi nói liền
    # nghe như nhiều clip ghép lại. Dấu câu gốc + director_notes đủ để tạo
    # khoảng lấy hơi mềm mà không làm biến dạng transcript.
    return text


__all__ = ["render_tts_markup"]
