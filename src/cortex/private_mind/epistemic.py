# -*- coding: utf-8 -*-
"""
cortex/private_mind/epistemic.py
================================

Bộ phân loại "epistemic self-awareness" thuần khiết (pure) cho private mind.

Triết lý (Cartesian): một claim không có Public_Anchor mặc định rơi vào *nghi ngờ*
(`speculative` -> `ungrounded_aware`), KHÔNG phải rơi vào niềm tin sai (false
certainty). Chỉ khi ERIS khẳng định `sincere` mà không có anchor thì mới là
confabulation (`ungrounded_blind`).

Module này CỐ TÌNH không phụ thuộc vào bất kỳ module private_mind nào khác và
không chạm vào time, RNG hay I/O. Mọi hàm ở đây là pure function của tham số đầu
vào, nên tính xác định (determinism) và tính tương đương giữa các producer (model
adapter vs deterministic fallback) đúng theo thiết kế (Property 2, Req 1.7/4.7).
"""

from __future__ import annotations

from typing import Literal

EpistemicStance = Literal["grounded", "ungrounded_aware", "ungrounded_blind"]
Intent = Literal["sincere", "playful", "teasing", "speculative"]

VALID_INTENTS: frozenset[str] = frozenset({"sincere", "playful", "teasing", "speculative"})
VALID_STANCES: frozenset[str] = frozenset({"grounded", "ungrounded_aware", "ungrounded_blind"})
DELIBERATE_INTENTS: frozenset[str] = frozenset({"playful", "teasing", "speculative"})

CONFABULATION_BLOCKED = "epistemic_confabulation_blocked"
SELF_KNOWLEDGE_FAILURE = "epistemic_self_knowledge_failure"


def normalize_intent(value: object) -> str | None:
    """Trả về một intent hợp lệ, hoặc None nếu thiếu/ngoài tập hợp (out-of-set).

    Pure: chỉ phụ thuộc vào `value`. Chuẩn hoá về chữ thường + bỏ khoảng trắng.
    """
    v = str(value or "").strip().lower()
    return v if v in VALID_INTENTS else None


def resolve_intent(declared: object, *, has_anchor: bool) -> str:
    """Req 2 — mặc định kiểu Cartesian.

    - declared hợp lệ  -> giữ nguyên;
    - declared vắng/ngoài tập:
        * có anchor   -> `sincere`;
        * không anchor -> `speculative` (nghi ngờ, vẫn được phép nổi lên).
    """
    norm = normalize_intent(declared)
    if norm is not None:
        return norm
    return "sincere" if has_anchor else "speculative"


def classify_epistemic_stance(*, has_anchor: bool, intent: str) -> str:
    """Req 1 — ánh xạ tổng (total), thuần khiết: (has_anchor, intent) -> stance.

    - có anchor                        -> `grounded` (anchor lấn át intent, Req 1.2);
    - không anchor + intent deliberate -> `ungrounded_aware` (Req 1.3);
    - không anchor + còn lại (sincere)  -> `ungrounded_blind` (Req 1.4).
    """
    if has_anchor:
        return "grounded"
    if intent in DELIBERATE_INTENTS:
        return "ungrounded_aware"
    # sincere, hoặc bất cứ thứ gì không deliberate, mà không có anchor
    return "ungrounded_blind"
